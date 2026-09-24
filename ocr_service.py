from settings import ROOT, CPU_THREADS
import json
import os
import gc
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import psutil
from PIL import Image, ImageDraw, ImageFont, ImageOps
from layout import entries_from_result, organize, recovery_regions, accept_recovery
from stage_timer import time_stages

_engine = None
_lock = threading.RLock()
_load_seconds = 0.0
_engine_threads = CPU_THREADS


def engine_threads():
    return _engine_threads if _engine is not None else None

def get_engine(cpu_threads=None):
    global _engine, _load_seconds, _engine_threads
    from cpu_resources import normalize_threads
    target=normalize_threads(cpu_threads)
    with _lock:
        if _engine is None or _engine_threads!=target:
            _engine=None
            from seal_tools import reset_engine
            reset_engine()
            gc.collect()
            for key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):
                os.environ[key]=str(target)
            from windows_model_compat import model_directory
            from paddleocr import PaddleOCR
            model_root = ROOT / 'models' / 'official_models'
            det_dir = model_root / 'PP-OCRv6_medium_det'
            rec_dir = model_root / 'PP-OCRv6_medium_rec'
            for directory in (det_dir, rec_dir):
                if directory.exists() and not all((directory / name).is_file() for name in ('inference.json', 'inference.pdiparams')):
                    raise FileNotFoundError(f'本地模型目录不完整：{directory}')
            start = time.perf_counter()
            _engine = PaddleOCR(
                lang='ch', ocr_version='PP-OCRv6', device='cpu', engine='paddle',
                **({'text_detection_model_dir': model_directory(det_dir)} if det_dir.is_dir() else {}),
                **({'text_recognition_model_dir': model_directory(rec_dir)} if rec_dir.is_dir() else {}),
                use_doc_orientation_classify=False, use_doc_unwarping=False,
                use_textline_orientation=False, cpu_threads=target,
                # Paddle 3.3.1 Windows oneDNN cannot convert this v6 PIR attribute.
                enable_mkldnn=False,
            )
            _engine_threads=target
            _engine._pm_cpu_threads=target
            _load_seconds = time.perf_counter() - start
        return _engine


class ResourceMeter:
    def __enter__(self):
        self.process = psutil.Process()
        self.before = self.process.memory_info().rss
        self.peak = self.before
        self.cpu_before = sum(self.process.cpu_times()[:2])
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.start = time.perf_counter()
        self.thread.start()
        return self

    def _sample(self):
        while not self.done.wait(0.05):
            self.peak = max(self.peak, self.process.memory_info().rss)

    def __exit__(self, *_):
        self.seconds = time.perf_counter() - self.start
        cpu_seconds = sum(self.process.cpu_times()[:2]) - self.cpu_before
        self.after = self.process.memory_info().rss
        self.peak = max(self.peak, self.after)
        self.done.set()
        self.thread.join(timeout=1)
        cores = psutil.cpu_count() or 1
        self.stats = {
            'ocr_seconds': round(self.seconds, 3),
            'memory_before_mib': round(self.before / 2**20, 1),
            'memory_after_mib': round(self.after / 2**20, 1),
            'memory_delta_mib': round((self.after - self.before) / 2**20, 1),
            'memory_peak_sampled_mib': round(self.peak / 2**20, 1),
            'cpu_seconds': round(cpu_seconds, 3),
            'cpu_average_machine_percent': round(100 * cpu_seconds / self.seconds / cores, 1),
            'cpu_average_one_core_percent': round(100 * cpu_seconds / self.seconds, 1),
            'logical_processors': cores, 'cpu_threads': _engine_threads,
        }


def recognize(path, recovery=True, document_type=None, options=None):
    from runtime_options import normalize_options
    from parsers.schema import PROFILES
    settings=normalize_options(options)
    recovery=recovery and settings["recovery"]
    request_start = time.perf_counter()
    if document_type not in (None, 'auto', *PROFILES):
        raise ValueError('不支持的证照类型。')
    if not path:
        raise ValueError('请先上传 JPG、JPEG 或 PNG 图片。')
    path = Path(path)
    if path.suffix.lower() not in {'.jpg', '.jpeg', '.png'}:
        raise ValueError('仅支持 JPG、JPEG 和 PNG 图片。')
    with Image.open(path) as source:
        if source.format not in {'JPEG', 'PNG'}:
            raise ValueError('图片实际格式必须是 JPEG 或 PNG。')
        if source.width * source.height > 25_000_000:
            raise ValueError('图片超过 2500 万像素，请缩小后再上传。')
        original = ImageOps.exif_transpose(source).convert('RGB')
    with _lock:
        resource_start=time.perf_counter()
        engine = get_engine(settings['cpu_threads'])
        resource_prepare_seconds=time.perf_counter()-resource_start
        # Paddle's numpy image input uses BGR channel order.
        bgr = np.asarray(original)[:, :, ::-1].copy()
        scaled = max(original.size) > settings['det_limit']
        detection_limit = settings['det_limit'] if scaled else None
        supplements = []
        recovery_warnings = []
        structured = None
        structure_seconds = 0.0
        local_check_seconds = 0.0
        field_region_seconds = 0.0
        seal_report={};seals=[];seal_seconds=0.0
        with ResourceMeter() as meter, time_stages(engine) as stages:
            primary_start = time.perf_counter()
            options = dict(text_det_limit_side_len=detection_limit, text_det_limit_type='max') if scaled else {}
            result = engine.predict(bgr, **options)
            primary_seconds = time.perf_counter() - primary_start
            primary_detection_seconds = stages['detection_seconds']
            primary_recognition_seconds = stages['recognition_seconds']
            primary = [item.json for item in result]
            primary = [json.loads(item) if isinstance(item, str) else item for item in primary]
            entries = entries_from_result(primary)
            recovery_start = time.perf_counter()
            regions = recovery_regions(entries, original.size, max_regions=3) if scaled and recovery else []
            for index, region in enumerate(regions):
                # Bound extra work; finish an in-flight region instead of interrupting native inference.
                if time.perf_counter() - recovery_start > 8:
                    recovery_warnings.append('局部补识别达到时间预算，其余区域保留原始结果。')
                    break
                x1, y1, x2, y2 = region['box']
                try:
                    sub_result = engine.predict(bgr[y1:y2, x1:x2].copy(), text_det_limit_side_len=960, text_det_limit_type='max')
                    sub_raw = [r.json for r in sub_result]
                    sub_raw = [json.loads(r) if isinstance(r, str) else r for r in sub_raw]
                    candidates = entries_from_result(sub_raw, source=f'recovery_{index}', offset=(x1, y1))
                    line_raw = []
                    fragments = [c['text'] for c in candidates if c['text'].strip() and c['score'] >= .95]
                    if region['reason'] == 'aligned_gap' and fragments:
                        # A short aligned gap can contain widely spaced characters that the
                        # detector splits. Reuse the existing pinned PaddleX line recognizer.
                        line_raw = [r.json for r in engine.paddlex_pipeline.text_rec_model([bgr[y1:y2, x1:x2].copy()])]
                        line_raw = [json.loads(r) if isinstance(r, str) else r for r in line_raw]
                        line = line_raw[0].get('res', line_raw[0]) if line_raw else {}
                        value = line.get('rec_text', '')
                        if (line.get('rec_score', 0) >= .95 and 2 <= len(value) <= 10
                                and all(fragment in value for fragment in fragments)
                                and len(value) >= sum(len(fragment) for fragment in fragments)):
                            candidates = [dict(id=f'recovery_{index}:line', text=value,
                                score=float(line['rec_score']), box=[x1,y1,x2,y2],
                                polygon=[[x1,y1],[x2,y1],[x2,y2],[x1,y2]], source=f'recovery_{index}')]
                    accepted = accept_recovery(entries, candidates, region)
                    entries.extend(accepted)
                    supplements.append(dict(region, raw=sub_raw, line_recognition_raw=line_raw, accepted_ids=[e['id'] for e in accepted],
                                            candidates=candidates))
                except Exception as exc:
                    logging.exception('Local recovery failed for region %s', region)
                    recovery_warnings.append(f'局部补识别失败，原始结果已保留：{type(exc).__name__}')
            recovery_seconds = time.perf_counter() - recovery_start
            seal_start=time.perf_counter()
            if settings['seal_enhance'] or settings['seal_text']:
                from seal_tools import enhance, recognize_seals
                if settings['seal_enhance']:
                    try: entries,seal_report=enhance(engine,bgr,entries,budget=12 if settings['preset']=='precise' else 6)
                    except Exception as exc:
                        logging.exception('Seal enhancement failed')
                        recovery_warnings.append('印章区域增强失败，保留原始识别。')
                if settings['seal_text']:
                    try: seals=recognize_seals(bgr,seal_report.get('regions'),engine=engine)
                    except Exception as exc:
                        logging.exception('Seal text recognition failed')
                        recovery_warnings.append('印章文字识别失败，正文结果已保留。')
            seal_seconds=time.perf_counter()-seal_start
            layout_start = time.perf_counter()
            layout = organize(entries)
            layout_seconds = time.perf_counter() - layout_start
            if document_type:
                from parsers.common import parse_document
                structure_start = time.perf_counter()
                local_cache = {}

                def recover_fields(regions):
                    nonlocal field_region_seconds
                    records=[]
                    start=time.perf_counter()
                    for i,region in enumerate(regions[:2]):
                        if time.perf_counter()-start>4:
                            recovery_warnings.append('字段局部检测达到时间预算，其余字段留待核对。')
                            break
                        x1,y1,x2,y2=map(int,region['box'])
                        region_start=time.perf_counter()
                        try:
                            local_result=engine.predict(bgr[y1:y2,x1:x2].copy(),text_det_limit_side_len=960,text_det_limit_type='max')
                            data=[r.json for r in local_result]
                            data=[json.loads(r) if isinstance(r,str) else r for r in data]
                            additions=entries_from_result(data,source=f'field_region_{i}',offset=(x1,y1))
                            records.append(dict(region,raw=data,entries=additions,seconds=round(time.perf_counter()-region_start,4)))
                        except Exception as exc:
                            logging.exception('Field region failed')
                            records.append(dict(region,error=type(exc).__name__,entries=[],seconds=round(time.perf_counter()-region_start,4)))
                    field_region_seconds+=time.perf_counter()-start
                    return records

                def reread(entry, suppress_red):
                    nonlocal local_check_seconds
                    cache_key = (tuple(tuple(p) for p in entry['polygon']), bool(suppress_red))
                    if cache_key in local_cache:
                        return dict(local_cache[cache_key], cached=True)
                    if len(local_cache) >= 5 or local_check_seconds > 4:
                        return {'error':'local_check_budget','text':'','score':0,
                                'reason':'字段局部复读达到预算，保留首轮候选供核对'}
                    start = time.perf_counter()
                    try:
                        # Rectify the original polygon, with a small neutral border.
                        # No extra detector pass, model, or change to general OCR.
                        import cv2
                        poly = np.asarray([entry['polygon']], dtype=np.float32)
                        crop = list(engine.paddlex_pipeline._crop_by_polys(bgr, poly))[0]
                        if suppress_red:
                            crop = np.repeat(crop[:, :, 2:3], 3, axis=2)
                        pad = max(2, round(crop.shape[0] * .10))
                        crop = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
                        results = [r.json for r in engine.paddlex_pipeline.text_rec_model([crop])]
                        results = [json.loads(r) if isinstance(r, str) else r for r in results]
                        data = results[0].get('res', results[0]) if results else {}
                        answer = {'text': data.get('rec_text', ''), 'score': data.get('rec_score', 0),
                                'method': 'red_channel' if suppress_red else 'padded_line',
                                'polygon': entry['polygon'], 'raw': results,
                                'seconds': round(time.perf_counter() - start, 4)}
                        local_cache[cache_key] = answer
                        return answer
                    except Exception as exc:
                        logging.exception('Field local check failed for %s', entry['id'])
                        return {'error': type(exc).__name__, 'text': '', 'score': 0}
                    finally:
                        local_check_seconds += time.perf_counter() - start

                structured = parse_document(entries, original.size, image=original, reread=reread if settings['local_review'] else None,
                                            document_type=document_type, recover=recover_fields if recovery else None)
                structure_seconds = time.perf_counter() - structure_start
        artifact_start = time.perf_counter()
        raw = {'primary': primary, 'recovery': supplements,'seal_enhancement':seal_report,'seals':seals}
        if structured is not None:
            raw['structured'] = structured
        rows = []
        annotated = original.copy()
        draw = ImageDraw.Draw(annotated)
        font = ImageFont.truetype('msyh.ttc', 18)
        for entry in layout['ordered_entries']:
            number = len(rows) + 1
            origin = '原始' if entry['source'] == 'primary' else '局部补识别'
            rows.append([number, entry['text'], entry['score'], origin])
            polygon = [tuple(map(int, point)) for point in entry['polygon']]
            color = '#d12e35' if origin == '原始' else '#007e78'
            draw.line(polygon + [polygon[0]], fill=color, width=3)
            draw.text((polygon[0][0], max(0, polygon[0][1] - 24)), str(number), font=font, fill=color)
        text = layout['text']
        original_text = '\n'.join(t for page in primary for t in page.get('res', page).get('rec_texts', []))
        stats = dict(meter.stats, text_lines=len(rows), model_load_seconds=round(_load_seconds, 3),
                     image_width=original.width, image_height=original.height,
                     device='cpu', model='PP-OCRv6_medium_det + PP-OCRv6_medium_rec',
                     detector_max_side=detection_limit, primary_seconds=round(primary_seconds, 3),
                     primary_detection_seconds=round(primary_detection_seconds, 3),
                     primary_recognition_seconds=round(primary_recognition_seconds, 3),
                     primary_other_seconds=round(max(0, primary_seconds - primary_detection_seconds - primary_recognition_seconds), 3),
                     recovery_seconds=round(recovery_seconds, 3), layout_seconds=round(layout_seconds, 3),
                     document_type=document_type, structure_seconds=round(structure_seconds, 3),
                     field_local_check_seconds=round(local_check_seconds, 3),
                     field_region_ocr_seconds=round(field_region_seconds,3),
                     field_rules_seconds=round(max(0, structure_seconds-local_check_seconds-field_region_seconds), 3),
                     secondary_detection_seconds=round(max(0, stages['detection_seconds']-primary_detection_seconds), 3),
                     secondary_recognition_seconds=round(max(0, stages['recognition_seconds']-primary_recognition_seconds), 3),
                     geometry_seconds=structured.get('geometry_seconds', 0) if structured else 0,
                     recovery_regions=len(supplements), recovered_lines=sum(len(s['accepted_ids']) for s in supplements),
                     paragraphs=len(layout['paragraphs']), warnings=recovery_warnings,
                     options=settings,resource_prepare_seconds=round(resource_prepare_seconds,3),seal_seconds=round(seal_seconds,3),seal_text_count=len(seals))
        run_id = datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8]
        out = ROOT / 'outputs' / run_id
        out.mkdir()
        (out / 'result.json').write_text(json.dumps(primary, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / 'recovery.json').write_text(json.dumps(supplements, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / 'layout.json').write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / 'resources.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / 'text.txt').write_text(text, encoding='utf-8')
        (out / 'raw_text.txt').write_text(original_text, encoding='utf-8')
        annotated.save(out / 'ocr_boxes.png')
        original.save(out/'source.png')
        (out/'seals.json').write_text(json.dumps({'seals':seals,'enhancement':seal_report},ensure_ascii=False,indent=2),encoding='utf-8')
        if structured is not None:
            from parsers.presentation import save_evidence
            (out / 'structured.json').write_text(json.dumps(structured, ensure_ascii=False, indent=2), encoding='utf-8')
            (out / 'prefill.json').write_text(json.dumps(structured['prefill'], ensure_ascii=False, indent=2), encoding='utf-8')
            save_evidence(original, entries+structured.get('regional_entries',[]), structured, out)
        stats['artifact_seconds'] = round(time.perf_counter()-artifact_start, 3)
        stats['request_seconds'] = round(time.perf_counter()-request_start, 3)
        (out / 'resources.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
        with (ROOT / 'logs/recognition.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps(dict(run_id=run_id, **stats), ensure_ascii=False) + '\n')
        return original, text, rows, stats, raw, annotated, str(out)
