from settings import ROOT, CPU_THREADS
import atexit
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import webbrowser

import gradio as gr
import psutil
from ocr_service import get_engine, recognize

STATE = ROOT / 'work/server.json'
MODES = {
    '通用 OCR': None,
    '自动识别证照类型': 'auto',
    '营业执照字段提取': 'business_license',
    '医疗器械注册证字段提取': 'device_registration',
    '医疗器械生产许可证字段提取': 'device_production_license',
    '医疗器械经营许可证字段提取': 'device_operation_license',
}
logging.basicConfig(filename=ROOT / 'logs/app.log', encoding='utf-8', level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')


def run_ocr(path, recovery=True, mode='通用 OCR', field_key='enterprise_name'):
    try:
        if mode not in MODES:
            raise ValueError('请选择有效的识别模式。')
        document_type = MODES[mode]
        original, text, rows, stats, raw, annotated, output = recognize(path, recovery=recovery, document_type=document_type)
    except Exception as exc:
        logging.exception('OCR request failed')
        raise gr.Error(str(exc)) from exc
    summary = (
        f"**OCR 耗时：{stats['ocr_seconds']:.3f} 秒**　｜　识别 {len(rows)} 条文字\n\n"
        f"Python 内存：{stats['memory_before_mib']:.1f} → {stats['memory_after_mib']:.1f} MiB"
        f"　｜　采样峰值：{stats['memory_peak_sampled_mib']:.1f} MiB\n\n"
        f"识别期间 CPU 平均占整机：{stats['cpu_average_machine_percent']:.1f}%"
        f"　｜　推理线程：{stats['cpu_threads']}\n\n"
        f"首轮识别：{stats['primary_seconds']:.2f} 秒　｜　局部补识别：{stats['recovery_seconds']:.2f} 秒"
        f"　｜　补充 {stats['recovered_lines']} 条文字\n\n"
        f"首轮检测：{stats['primary_detection_seconds']:.2f} 秒　｜　首轮文字识别：{stats['primary_recognition_seconds']:.2f} 秒"
        f"　｜　字段处理：{stats['structure_seconds']:.3f} 秒\n\n"
        f"结果保存至：`{output}`"
    )
    if not rows:
        summary += '\n\n未检测到可识别文字，请检查图片清晰度与方向。'
    if stats['warnings']:
        summary += '\n\n' + '；'.join(stats['warnings'])
    original_text = '\n'.join(t for page in raw['primary'] for t in page.get('res', page).get('rec_texts', []))
    detail = ([], None, None, None, None, '', '')
    if document_type:
        from pathlib import Path
        from parsers.presentation import table_rows
        structured = raw.get('structured') or {}
        out = Path(output)
        field_data = structured.get('fields') or {}
        extracted = sum(bool(f.get('value')) and f.get('status') == '已提取' for f in field_data.values())
        review = sum(f.get('status') == '待核对' for f in field_data.values())
        missing = sum(f.get('status') == '未识别' for f in field_data.values())
        info = (f"**{structured.get('document_label') or structured.get('document_type', '未确定类型')}**"
                f"　｜　{structured.get('document_status', '未生成可靠结构化结果')}\n\n"
                f"已提取 {extracted} 项　｜　待核对 {review} 项　｜　未识别 {missing} 项\n\n"
                '所有已识别候选均返回并回填，待核对项保留提示，提交前由用户修改确认；'
                'OCR 分数和综合评分均不代表正确概率。')
        classification = structured.get('classification') or {}
        if isinstance(classification, dict):
            reasons = classification.get('reasons', classification.get('reason', []))
            if isinstance(reasons, str):
                reasons = [reasons]
            if reasons:
                info += '\n\n类型判断：' + '；'.join(map(str, reasons))
        if not field_data:
            info += '\n\n本图未进入已支持的字段解析，请查看类型判断及原始 OCR；必要时手动选择证照类型。'
        selected_key = field_key if field_key in field_data else next(iter(field_data), None)
        download = str(out/'prefill.json') if field_data and (out/'prefill.json').is_file() else None
        boxes = str(out/'license_fields.png') if field_data and (out/'license_fields.png').is_file() else None
        detail = (table_rows(structured), structured, download,
                  boxes, field_crop(output, selected_key), output, info)
    return (original, text, rows, summary, raw, annotated, stats, original_text, *detail)


def saved_fields(output):
    from pathlib import Path
    if not output:
        return None, {}
    folder = Path(output).resolve()
    if not folder.is_relative_to((ROOT/'outputs').resolve()):
        return None, {}
    try:
        structured_path = (folder/'structured.json').resolve()
        if not structured_path.is_relative_to(folder):
            return None, {}
        structured = json.loads(structured_path.read_text(encoding='utf-8'))
        fields = structured.get('fields') or {}
        if not isinstance(fields, dict):
            return None, {}
        return folder, {key: value for key, value in fields.items()
                        if isinstance(key, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key)
                        and isinstance(value, dict)}
    except (OSError, ValueError, TypeError):
        return None, {}


def field_crop(output, key):
    # Validate both the run directory and the actual stored field definitions.
    folder, fields = saved_fields(output)
    if folder is None or key not in fields:
        return None
    path = (folder/'fields'/f'{key}.png').resolve()
    return str(path) if path.is_relative_to(folder) and path.is_file() else None


def update_field_choices(output, selected=None):
    _, fields = saved_fields(output)
    choices = [(f"{field.get('number', index)} {field.get('label', key)}", key)
               for index, (key, field) in enumerate(fields.items(), 1)]
    value = selected if selected in fields else next(iter(fields), None)
    return gr.update(choices=choices, value=value, interactive=bool(choices))


def shutdown():
    def finish():
        time.sleep(1.5)
        STATE.unlink(missing_ok=True)
        logging.shutdown()
        os._exit(0)
    threading.Thread(target=finish, daemon=True).start()
    return '程序正在关闭，模型和内存将释放。下次双击 start.bat 即可重新启动。'


def build_ui():
    with gr.Blocks(title='通用 OCR 与医疗行业证照识别', analytics_enabled=False) as ui:
        gr.Markdown('# 通用 OCR 与医疗行业证照识别\nPP-OCRv6 · 本机 CPU · 图片仅在本机处理')
        with gr.Row():
            upload = gr.File(label='上传 JPG / JPEG / PNG 图片', file_types=['.jpg', '.jpeg', '.png'], type='filepath')
            with gr.Column():
                mode = gr.Radio(list(MODES), value='通用 OCR', label='识别模式')
                run = gr.Button('开始识别', variant='primary')
                recovery = gr.Checkbox(value=True, label='大图异常区域补识别', info='小图保持原识别路径；大图最多补识别 3 个区域。')
                gr.Markdown('请上传文字方向正常的清晰图片。首次识别含推理预热；后续识别通常更快。')
                stop = gr.Button('关闭程序并释放内存')
        status = gr.Markdown()
        metrics = gr.Markdown('模型已就绪，上传图片后点击“开始识别”。')
        with gr.Row():
            original = gr.Image(label='原始图片', type='pil', interactive=False, buttons=['download', 'fullscreen'])
            annotated = gr.Image(label='文字检测框（编号对应下表）', type='pil', interactive=False, buttons=['download', 'fullscreen'])
        output_state = gr.State('')
        with gr.Column(visible=False) as license_panel:
            gr.Markdown('### 证照结构化字段')
            structure_info = gr.Markdown()
            fields = gr.Dataframe(headers=['编号', '字段', '回填值', 'OCR 候选', '状态', '最低 OCR 分', '综合评分', '核对说明'],
                                  datatype=['number', 'str', 'str', 'str', 'str', 'number', 'number', 'str'],
                                  interactive=False, wrap=True,
                                  column_widths=['4%', '10%', '21%', '21%', '8%', '8%', '8%', '20%'],
                                  label='字段与原图对应')
            with gr.Row():
                field_boxes = gr.Image(label='字段位置（青色：已提取；橙色：待核对）', interactive=False, buttons=['download', 'fullscreen'])
                with gr.Column():
                    field_choice = gr.Dropdown(choices=[], value=None, interactive=False,
                                               label='识别后选择字段查看原图局部')
                    crop = gr.Image(label='原图局部（保留原始印迹）', interactive=False, buttons=['download', 'fullscreen'])
            prefill_file = gr.File(label='下载回填字段 JSON', interactive=False)
            with gr.Accordion('字段证据与核对状态 JSON', open=False):
                structure_json = gr.JSON(label='结构化字段、坐标与局部复读记录')
        mode.change(lambda value: gr.update(visible=value != '通用 OCR'), inputs=mode, outputs=license_panel, queue=False, api_name=False)
        field_choice.change(field_crop, inputs=[output_state, field_choice], outputs=crop, queue=False, api_name=False)
        text = gr.Textbox(label='完整文字（按位置整理段落）', lines=10, buttons=['copy'])
        gr.Markdown('段落按文字位置整理，不自动改字；复杂版式请与原图核对。红框为首轮结果，青框为局部补识别。')
        with gr.Accordion('查看首轮原始文字（原顺序）', open=False):
            original_text = gr.Textbox(label='首轮原始文字', lines=8, buttons=['copy'], interactive=False)
        rows = gr.Dataframe(headers=['序号', '文字', '识别置信度', '来源'], datatype=['number', 'str', 'number', 'str'], interactive=False, label='逐条识别结果（整理顺序）')
        with gr.Accordion('PaddleOCR 原始结构化 JSON（首轮与局部补识别）', open=False):
            raw = gr.JSON(label='原始结果')
        with gr.Accordion('完整资源统计', open=False):
            resources = gr.JSON(label='资源统计')
            gr.Markdown('内存为本 Python 进程工作集（MiB）；峰值每 50 ms 采样。CPU 占用按本机逻辑处理器数量归一化；OCR 耗时包含首轮、局部补识别、段落整理，以及所选模式的字段处理，不含模型加载、上传、绘图与保存。')
        event = run.click(run_ocr, inputs=[upload, recovery, mode, field_choice], outputs=[original, text, rows, metrics, raw, annotated, resources, original_text,
                  fields, structure_json, prefill_file, field_boxes, crop, output_state, structure_info],
                  api_name='recognize', concurrency_limit=1)
        event.then(update_field_choices, inputs=[output_state, field_choice], outputs=field_choice,
                   queue=False, api_name=False)
        stop.click(shutdown, outputs=status, queue=False, api_name=False)
    return ui


def main():
    # Serialize double-click launches without adding a resident supervisor.
    import filelock
    try:
        lock = filelock.FileLock(str(ROOT / 'work/server.lock'), timeout=0)
        lock.acquire()
    except filelock.Timeout:
        if STATE.exists():
            webbrowser.open(json.loads(STATE.read_text())['url'] + (''))
        print('OCR is already running or loading. Please wait.')
        return
    atexit.register(lock.release)
    from parsers.schema import PROFILES
    for key,spec in PROFILES.items():
        MODES.setdefault(spec.label+'字段提取',key)
    print('Loading PP-OCRv6 on CPU...', flush=True)
    get_engine()
    ui = build_ui()
    port = None
    for candidate in range(7860, 7880):
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', candidate))
                port = candidate
                break
            except OSError:
                pass
    if port is None:
        raise RuntimeError('本地端口 7860–7879 均被占用。')
    url = f'http://127.0.0.1:{port}'
    from starlette.middleware import Middleware
    from certificate_api import WorkbenchBoundary, attach_api
    ui.queue(max_size=8).launch(server_name='127.0.0.1', server_port=port, share=False,
                              inbrowser=False, prevent_thread_lock=True,
                              app_kwargs={'middleware': [Middleware(WorkbenchBoundary)]},
                              ssr_mode=False, max_file_size='30mb')
    attach_api(ui.app,shutdown=shutdown)
    STATE.write_text(json.dumps({'pid': os.getpid(), 'create_time': psutil.Process().create_time(), 'url': url}), encoding='utf-8')
    atexit.register(lambda: STATE.unlink(missing_ok=True))
    print(f'Local URL: {url}', flush=True)
    if '--no-browser' not in sys.argv:
        webbrowser.open(url + (''))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print('Closing OCR...', flush=True)
    finally:
        ui.close()


if __name__ == '__main__':
    main()
