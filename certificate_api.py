"""Local certificate form API; uses the same serialized OCR engine as Gradio."""
import logging
from pathlib import Path
import tempfile
import threading

from fastapi import FastAPI, File, HTTPException, UploadFile, Form
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from settings import ROOT
from parsers.schema import PROFILES

MAX_BYTES = 50 * 1024 * 1024
BUSY = threading.Lock()
from app_configuration import CONFIG


def attach_api(app,shutdown=None):
    api = FastAPI(docs_url=None, redoc_url=None)
    # Local files, loopback pages and explicitly configured business origins only.
    @api.middleware('http')
    async def local_api(request, call_next):
        if not request.url.path.startswith('/api/'):
            return await call_next(request)
        import re
        origin = request.headers.get('origin')
        allowed = origin is None or origin == 'null' or origin in CONFIG['allowed_origins'] or bool(re.fullmatch(r'https?://(127\.0\.0\.1|localhost)(:\d+)?', origin))
        if not allowed:
            return JSONResponse({'detail': '仅允许本机证照页面访问。'}, status_code=403)
        try:
            too_large = int(request.headers.get('content-length', '0')) > MAX_BYTES + 1024 * 1024
        except ValueError:
            too_large = True
        if request.method == 'OPTIONS':
            response = JSONResponse({})
        elif too_large:
            response = JSONResponse({'detail': '附件不可超过 50 MB。'}, status_code=413)
        else:
            response = await call_next(request)
        if origin:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            response.headers['Access-Control-Allow-Private-Network'] = 'true'
        return response

    @api.get('/health')
    def health():
        return {'service': 'certificate-ocr', 'api_version': 2, 'candidate_policy': 'return_all',
                'supported_types': list(PROFILES), 'busy': BUSY.locked(), 'max_file_bytes': MAX_BYTES}

    @api.get('/templates')
    def templates():
        return {key: {'label': spec.label, 'fields': [
            {'key': f.key, 'label': f.label, 'kind': f.kind, 'multiline': f.multiline, 'required': f.required}
            for f in spec.fields]} for key, spec in PROFILES.items()}

    @api.post('/ocr/{document_type}')
    async def ocr(document_type: str, file: UploadFile = File(...), page: int = Form(1), options: str = Form('{}'), password: str = Form('')):
        if document_type not in PROFILES and document_type not in ('general','auto'):
            raise HTTPException(400, '不支持的识别类型。')
        suffix = Path(file.filename or '').suffix.lower()
        from document_input import Document, SUFFIXES
        from runtime_options import normalize_options
        import json
        if suffix not in SUFFIXES:
            raise HTTPException(415, '支持 PDF、JPG、PNG、BMP、TIFF、WebP、GIF。')
        try: settings=normalize_options(json.loads(options))
        except (ValueError,TypeError) as exc: raise HTTPException(400,str(exc))
        if page<1: raise HTTPException(400,'页码从 1 开始。')
        if not BUSY.acquire(blocking=False):
            raise HTTPException(409, '已有证照正在识别，请稍后重试。')
        path = None
        rendered = None
        doc = None
        try:
            with tempfile.NamedTemporaryFile(dir=ROOT / 'cache/tmp', suffix=suffix, delete=False) as target:
                path = target.name
                total = 0
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise HTTPException(413, '附件不可超过 50 MB。')
                    target.write(chunk)
            if not total:
                raise HTTPException(400, '附件内容为空。')
            from ocr_service import recognize
            doc = await run_in_threadpool(Document, path, password=password, pages=str(page))
            rendered = str(Path(path).with_suffix('.rendered.png'))
            await run_in_threadpool(doc.render, page-1, settings['pdf_dpi'], rendered)
            result = await run_in_threadpool(recognize, rendered, document_type=None if document_type == 'general' else document_type, options=settings)
            _, text, rows, stats, raw, _, output = result
            structured = raw.get('structured') or {}
            return {'request_id': Path(output).name, 'requested_type': document_type,
                    'document_type': structured.get('document_type', 'general'),
                    'supported': bool(structured.get('supported')),
                    'message': structured.get('document_status', '全文识别完成，请根据原图填写字段。'),
                    'prefill': structured.get('prefill', {}), 'fields': structured.get('fields', {}),
                    'review_required': structured.get('review_required', []),
                    'classification': structured.get('classification', {}), 'text': text,
                    'rows': rows, 'ocr_seconds': stats['ocr_seconds'], 'requires_confirmation': True,
                    'page_number': page, 'document_pages': doc.count, 'stats': stats, 'seals': raw.get('seals',[]),
                    'page_notice': f'本次识别第 {page} 页，共 {doc.count} 页；批量逐页识别请使用 /api/jobs。' if doc.count>1 else ''}
        except HTTPException:
            raise
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            logging.exception('Certificate API recognition failed')
            raise HTTPException(500, '本地识别失败，请重试或检查 OCR 运行日志。') from exc
        finally:
            try:
                await file.close()
                if doc: doc.close()
                if rendered: Path(rendered).unlink(missing_ok=True)
                if path:
                    Path(path).unlink(missing_ok=True)
            finally:
                BUSY.release()
    from workbench_api import register
    register(app,api,BUSY,shutdown=shutdown)
    app.mount("/api", api)
