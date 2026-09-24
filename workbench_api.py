"""Local, persistent document jobs and certificate workbench."""
import json,threading,time,uuid,re,logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import File,Form,UploadFile,HTTPException,Body
from fastapi.responses import FileResponse,HTMLResponse,Response
from starlette.concurrency import run_in_threadpool
from settings import ROOT
from runtime_options import normalize_options,PRESETS
from document_input import Document,SUFFIXES
from parsers.schema import PROFILES
JOBS=ROOT/'outputs/jobs';JOBS.mkdir(exist_ok=True)
EXECUTOR=ThreadPoolExecutor(max_workers=1,thread_name_prefix='document')
GUARD=threading.RLock();ACTIVE={};MAX_UPLOAD=50*1024*1024

def write_json(path,value):
    # Windows readers / sync clients may briefly hold a file without delete sharing.
    with GUARD:
        temp=path.with_name(path.stem+'.'+uuid.uuid4().hex[:8]+'.tmp')
        temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        for attempt in range(6):
            try:temp.replace(path);return
            except PermissionError:
                if attempt==5:raise
                time.sleep(.03*(2**attempt))
def read_json(path):
    with GUARD:return json.loads(path.read_text('utf-8'))
def folder(id):
    if not re.fullmatch(r'[0-9a-f]{32}',id):raise HTTPException(404,'未找到识别任务。')
    p=JOBS/id
    if not (p/'job.json').is_file():raise HTTPException(404,'未找到识别任务。')
    return p
def state(id):
    with GUARD:return read_json(folder(id)/'job.json')
def update(id,**values):
    with GUARD:
        p=folder(id)/'job.json';data=read_json(p);data.update(values);write_json(p,data);return data
def recover_interrupted_jobs():
    for p in JOBS.glob('*/job.json'):
        try:
            data=read_json(p)
            if data['status'] in ('queued','running','cancelling'):
                data.update(status='interrupted',error='服务已重启，已完成页面保留；请重新提交剩余页。')
                write_json(p,data)
        except (ValueError,KeyError):logging.warning('Unreadable job %s',p.name)
def worker(id,password,busy_lock):
    p=folder(id);start=time.perf_counter();doc=None;completed=[];failures=[]
    try:
        data=state(id)
        with busy_lock:
            if ACTIVE[id].is_set():update(id,status='cancelled');return
            update(id,status='running',started_at=time.time(),message='正在打开文档')
            doc=Document(p/('input'+data['extension']),password=password,pages=data['page_range'])
            options=data['options'];update(id,page_count=doc.count,selected_pages=[x+1 for x in doc.pages],total=len(doc.pages))
            for index in doc.pages:
                if ACTIVE[id].is_set():break
                number=index+1;update(id,current_page=number,message=f'正在识别第 {number} 页')
                image_path=p/f'page_{number}.png'
                try:
                    size=doc.render(index,options['pdf_dpi'],image_path)
                    from ocr_service import recognize
                    _,text,rows,stats,raw,_,output=recognize(image_path,document_type=None if data['document_type']=='general' else data['document_type'],options=options)
                    structured=raw.get('structured') or {}
                    page={'page':number,'success':True,'image_size':size,
                          'preview_url':f'/api/jobs/{id}/pages/{number}/image',
                          'document_type':structured.get('document_type','general'),
                          'document_label':structured.get('document_label','通用文字'),
                          'supported':structured.get('supported',False),
                          'message':structured.get('document_status','全文识别完成'),
                          'prefill':structured.get('prefill',{}),'fields':structured.get('fields',{}),
                          'review_required':structured.get('review_required',[]),
                          'text':text,'rows':rows,'stats':stats,'seals':raw.get('seals',[]),
                          'seal_enhancement':raw.get('seal_enhancement',{}),
                          'output_id':Path(output).name}
                except Exception as exc:
                    logging.exception('Page recognition failed')
                    page={'page':number,'success':False,'error':str(exc)}
                    failures.append(number)
                write_json(p/f'result_{number}.json',page)
                completed.append(number);update(id,completed_pages=completed,failed_pages=failures,done=len(completed),elapsed=round(time.perf_counter()-start,2))
            status='cancelled' if ACTIVE[id].is_set() else 'partial' if failures and len(failures)<len(completed) else 'failed' if failures else 'completed'
            update(id,status=status,message='已停止，已完成页面保留' if status=='cancelled' else '识别完成',elapsed=round(time.perf_counter()-start,2))
        pages=[read_json(p/f'result_{i}.json') for i in completed]
        write_json(p/'result.json',{'product':'MedCert OCR','job':state(id),'pages':pages})
        (p/'text.txt').write_text('\n\n'.join(f"第 {a['page']} 页\n"+a.get('text',a.get('error','')) for a in pages),encoding='utf-8')
    except Exception as exc:
        logging.exception('Document failed')
        update(id,status='failed',error=str(exc),elapsed=round(time.perf_counter()-start,2))
    finally:
        if doc:doc.close()
        with GUARD:ACTIVE.pop(id,None)

def register(app,api,busy_lock,shutdown=None):
    recover_interrupted_jobs()
    @app.get('/',response_class=HTMLResponse,include_in_schema=False)
    def workbench():
        html=(ROOT/'workbench/index.html').read_text('utf-8')
        html=html.replace('/* WORKBENCH_CSS */',(ROOT/'workbench/styles.css').read_text('utf-8'))
        html=html.replace('/* WORKBENCH_JS */',(ROOT/'workbench/app.js').read_text('utf-8'))
        return HTMLResponse(html,headers={'Cache-Control':'no-store'})
    route=app.router.routes.pop();app.router.routes.insert(0,route)
    @api.post('/shutdown')
    def shutdown_service():
        if shutdown is None:raise HTTPException(503,'请使用 stop.bat 关闭服务。')
        with GUARD:
            if ACTIVE or busy_lock.locked():raise HTTPException(409,'请先停止或完成识别任务，再关闭服务。')
            return {'message':shutdown()}
    @api.get('/capabilities')
    def capabilities():
        return {'product':'MedCert OCR','version':'0.1.0','max_file_bytes':MAX_UPLOAD,'max_pages':50,
                'formats':sorted(SUFFIXES),'presets':PRESETS,
                'types':{key:spec.label for key,spec in PROFILES.items()},
                'cpu':__import__('cpu_resources').capabilities()}
    @api.get('/resources')
    def resources():
        from cpu_resources import snapshot
        return snapshot()
    @api.post('/jobs',status_code=202)
    async def create_job(file:UploadFile=File(...),document_type:str=Form('auto'),options:str=Form('{}'),
                         page_range:str=Form(''),password:str=Form('')):
        if document_type not in ('auto','general',*PROFILES):raise HTTPException(400,'证照类型不支持。')
        extension=Path(file.filename or '').suffix.lower()
        if extension not in SUFFIXES:raise HTTPException(415,'文件格式不支持。')
        try:settings=normalize_options(json.loads(options))
        except (ValueError,TypeError) as exc:raise HTTPException(400,str(exc))
        if len(page_range)>256:raise HTTPException(400,'页码范围过长。')
        with GUARD:
            if len(ACTIVE)>=4:raise HTTPException(429,'当前最多保留 4 个进行中的任务，请稍后提交。')
            id=uuid.uuid4().hex;p=JOBS/id;p.mkdir();ACTIVE[id]=threading.Event()
        try:
            count=0
            with (p/('input'+extension)).open('wb') as out:
                while chunk:=await file.read(1024*1024):
                    count+=len(chunk)
                    if count>MAX_UPLOAD:raise HTTPException(413,'文件不可超过 50 MB。')
                    out.write(chunk)
            if not count:raise HTTPException(400,'文件内容为空。')
            data={'id':id,'filename':Path(file.filename).name,'extension':extension,'bytes':count,
                  'document_type':document_type,'options':settings,'page_range':page_range,
                  'status':'queued','created_at':time.time(),'done':0,'total':0,
                  'completed_pages':[],'failed_pages':[],'message':'等待识别'}
            write_json(p/'job.json',data)
            EXECUTOR.submit(worker,id,password,busy_lock)
            return data
        except Exception:
            with GUARD:ACTIVE.pop(id,None)
            raise
        finally:await file.close()
    @api.get('/jobs')
    def jobs():
        result=[]
        for p in sorted(JOBS.glob('*/job.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:100]:
            try:result.append(read_json(p))
            except ValueError:continue
        return result
    @api.get('/jobs/{id}')
    def get_job(id:str):return state(id)
    @api.post('/jobs/{id}/cancel')
    def cancel(id:str):
        data=state(id)
        with GUARD:
            if id in ACTIVE:ACTIVE[id].set();return update(id,status='cancelling',message='当前页结束后停止')
        return data
    @api.get('/jobs/{id}/pages/{number}')
    def get_page(id:str,number:int):
        p=folder(id)/f'result_{number}.json'
        if not p.is_file():raise HTTPException(404,'此页尚未完成。')
        return read_json(p)
    @api.post('/jobs/{id}/pages/{number}/review')
    def review_page(id:str,number:int,body:dict=Body(...)):
        with GUARD:
            p=folder(id)/f'result_{number}.json'
            if not p.is_file():raise HTTPException(404,'此页尚未完成。')
            data=read_json(p);fields=data.get('fields',{})
            values=body.get('values')
            if not isinstance(values,dict) or set(values)-set(fields):raise HTTPException(400,'修订字段不属于当前页。')
            if any(not isinstance(v,str) or len(v)>32768 for v in values.values()):raise HTTPException(400,'修订值格式不正确或过长。')
            data['review']={'values':values,'saved_at':time.time(),'source':'user'}
            write_json(p,data);return data
    @api.get('/jobs/{id}/pages/{number}/image')
    def get_image(id:str,number:int):
        p=folder(id)/f'page_{number}.png'
        if not p.is_file():raise HTTPException(404,'未找到该页图片。')
        return FileResponse(p,media_type='image/png')
    @api.get('/jobs/{id}/download/{kind}')
    def download(id:str,kind:str):
        if kind not in ('json','txt'):raise HTTPException(404)
        p=folder(id)/('result.json' if kind=='json' else 'text.txt')
        if not p.is_file():raise HTTPException(409,'文档尚未结束，已完成页可逐页查看。')
        if kind=='json':
            with GUARD:
                job=state(id)
                pages=[read_json(folder(id)/f'result_{i}.json') for i in job['completed_pages']]
                payload={'product':'MedCert OCR','job':job,'pages':pages}
                write_json(p,payload)
                return Response(json.dumps(payload,ensure_ascii=False,indent=2),media_type='application/json',
                    headers={'Content-Disposition':f'attachment; filename="MedCert-OCR-{id[:8]}.json"'})
        return FileResponse(p,filename='MedCert-OCR-'+id[:8]+'.'+kind)
