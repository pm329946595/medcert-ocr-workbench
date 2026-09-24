"""Stamp-aware local recovery. Original text and all alternate reads remain auditable."""
import json,time,re
from difflib import SequenceMatcher
import cv2,numpy as np
from PIL import Image
from settings import ROOT,CPU_THREADS
from layout import entries_from_result,accept_recovery
from parsers.visual_risk import ink_risk
_seal_engine=None
_seal_threads=None
def reset_engine():
    global _seal_engine,_seal_threads
    _seal_engine=None;_seal_threads=None
def compact(s): return re.sub(r'\s+','',s)
def raw(result):
    x=result.json
    return json.loads(x) if isinstance(x,str) else x
def seal_regions(risk,w,h):
    k=max(3,round(min(w,h)*.012))
    mask=cv2.dilate(risk.red_mask,np.ones((k,k),np.uint8))
    n,_,stats,_=cv2.connectedComponentsWithStats(mask)
    boxes=[]
    for x,y,bw,bh,area in sorted(stats[1:],key=lambda s:-s[4]):
        if bw<25 or bh<25 or bw*bh>w*h*.4:continue
        pad=max(10,round(max(bw,bh)*.1))
        boxes.append([max(0,int(x-pad)),max(0,int(y-pad)),min(w,int(x+bw+pad)),min(h,int(y+bh+pad))])
        if len(boxes)==3:break
    return boxes
def choose_variant(original,variants):
    good=[v for v in variants if v['text'] and v['score']>=.96]
    for item in good:
        agree=[v for v in good if compact(v['text'])==compact(item['text'])]
        if len(agree)>=2 and SequenceMatcher(None,compact(original),compact(item['text'])).ratio()>=.65:
            return max(agree,key=lambda v:v['score'])
    return None
def enhance(engine,bgr,entries,budget=8):
    start=time.perf_counter();rgb=Image.fromarray(bgr[:,:,::-1]);risk=ink_risk(rgb,entries)
    h,w=bgr.shape[:2];regions=seal_regions(risk,w,h)
    report={'method':'red_channel_contrast_consensus','reads':[],'added_ids':[],'regions':regions}
    if not regions:return entries,report
    out=[dict(e) for e in entries]
    selected=[e for e in out if risk.for_box(e['box'])['red_stamp_overlap']]
    selected.sort(key=lambda e:e['score'])
    for entry in selected[:5]:
        if time.perf_counter()-start>budget:break
        x1,y1,x2,y2=map(int,entry['box']);pad=max(3,(y2-y1)//6)
        crop=bgr[max(0,y1-pad):min(h,y2+pad),max(0,x1-pad):min(w,x2+pad)]
        if not crop.size:continue
        red=crop[:,:,2];contrast=cv2.createCLAHE(clipLimit=1.5,tileGridSize=(4,4)).apply(red)
        variants=[]
        for name,gray in [('red_channel',red),('red_contrast',contrast)]:
            value=raw(list(engine.paddlex_pipeline.text_rec_model([np.repeat(gray[:,:,None],3,axis=2)]))[0])
            value=value.get('res',value)
            variants.append({'method':name,'text':value.get('rec_text',''),'score':float(value.get('rec_score',0))})
        pick=choose_variant(entry['text'],variants)
        record={'source_id':entry['id'],'original':entry['text'],'variants':variants,'accepted':bool(pick and compact(pick['text'])!=compact(entry['text']))}
        entry['seal_candidates']=variants
        if record['accepted']:
            entry['original_text']=entry['text'];entry['text']=pick['text'];entry['score']=pick['score'];entry['seal_corrected']=True
        report['reads'].append(record)
    # Re-detect one affected region to find text absent from the first pass.
    if regions and time.perf_counter()-start<budget:
        x1,y1,x2,y2=regions[0];crop=bgr[y1:y2,x1:x2];clean=np.repeat(crop[:,:,2:3],3,axis=2)
        data=[raw(r) for r in engine.predict(clean,text_det_limit_side_len=960,text_det_limit_type='max')]
        candidates=entries_from_result(data,source='seal_recovery',offset=(x1,y1))
        additions=accept_recovery(out,candidates,{'box':regions[0],'reason':'seal_overlap'})
        out.extend(additions);report['added_ids']=[e['id'] for e in additions]
    report['seconds']=round(time.perf_counter()-start,3)
    return out,report
def circle_variants(crop):
    """Unwrap a visible circular red outline; no certificate text is inferred."""
    b,g,r=cv2.split(crop.astype(np.float32));delta=np.clip(r-np.maximum(b,g),0,255)
    contours,_=cv2.findContours((delta>25).astype(np.uint8)*255,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    if not contours:return []
    contour=max(contours,key=cv2.contourArea);(x,y),radius=cv2.minEnclosingCircle(contour)
    _,_,w,h=cv2.boundingRect(contour)
    if radius<24 or not .65<w/max(1,h)<1.5 or cv2.contourArea(contour)<.15*np.pi*radius*radius:return []
    output=[]
    for name,clean in [('red_ink',np.uint8(255-np.clip(delta*3,0,255))),('green_channel',crop[:,:,1])]:
        polar=cv2.warpPolar(clean,(int(radius*1.02),900),(x,y),radius*1.02,cv2.WARP_POLAR_LINEAR|cv2.WARP_FILL_OUTLIERS)
        for ratio in (.5,.6):
            strip=np.flip(polar[:,int(radius*ratio):int(radius*.96)].T,axis=0).copy()
            strip=np.roll(strip,-225,axis=1)
            strip=cv2.copyMakeBorder(strip,6,6,5,5,cv2.BORDER_CONSTANT,value=255)
            output.append((name+'_'+str(ratio),cv2.cvtColor(strip,cv2.COLOR_GRAY2BGR)))
    return output

def read_variants(engine,variants):
    output=[]
    for method,crop in variants:
        data=raw(list(engine.paddlex_pipeline.text_rec_model([crop]))[0]);data=data.get('res',data)
        if data.get('rec_text'):output.append({'method':method,'text':data['rec_text'],'score':float(data.get('rec_score',0))})
    return output

def recognize_seals(bgr,regions=None,engine=None):
    global _seal_engine,_seal_threads
    from paddleocr import SealTextDetection
    from paddlex.inference.pipelines.components import CropByPolys
    if engine is None:
        from ocr_service import get_engine
        engine=get_engine()
    target=getattr(engine,'_pm_cpu_threads',CPU_THREADS)
    if _seal_engine is None or _seal_threads!=target:
        det=ROOT/'models/official_models/PP-OCRv4_server_seal_det'
        if det.is_dir() and not (det/'inference.pdiparams').exists():raise ValueError('本地印章模型目录不完整。')
        from windows_model_compat import model_directory
        _seal_engine=SealTextDetection(model_name='PP-OCRv4_server_seal_det',**({'model_dir':model_directory(det)} if det.is_dir() else {}),
            device='cpu',enable_mkldnn=False,cpu_threads=target)
        _seal_threads=target
    rectify=CropByPolys(det_box_type='poly')
    h,w=bgr.shape[:2];answers=[]
    for box in (regions or [[0,0,w,h]])[:3]:
        x1,y1,x2,y2=box;crop=bgr[y1:y2,x1:x2].copy()
        ring=read_variants(engine,circle_variants(crop))
        if ring:
            pick=max(ring,key=lambda a:a['score'])
            answers.append(dict(pick,region=box,status='待核对',method='circular_unwrap',variants=ring))
        for result in _seal_engine.predict(crop,limit_side_len=960,limit_type='max'):
            data=raw(result);data=data.get('res',data)
            polys=data.get('dt_polys',[])
            for poly in polys:
                snippets=rectify(crop,[np.asarray(poly,dtype=np.float32)])
                for snippet in snippets:
                    rotations=(0,1,3) if snippet.shape[0]>snippet.shape[1] else (0,2)
                    variants=read_variants(engine,[(f'rotation_{k*90}',np.rot90(snippet,k).copy()) for k in rotations])
                    if variants:
                        pick=max(variants,key=lambda a:a['score'])
                        absolute=(np.asarray(poly)+np.array([x1,y1])).tolist()
                        answers.append(dict(pick,region=box,polygon=absolute,status='待核对',method='curved_detector',variants=variants))
    return answers
