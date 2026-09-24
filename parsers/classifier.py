"""Conservative OCR/geometry document routing, independent of paths and corpus labels.

Scores are rule evidence scores, never probabilities or field correctness scores.
Recognition does not imply a parser exists for the detected document type.
"""
import math
import re
import unicodedata
from statistics import median

from .schema import PROFILES, TYPE_LABELS


TITLE_PATTERNS = {
    'business_license': (r'营业执照',),
    'device_operation_license': (r'医疗器械经营许可证',),
    'device_production_license': (r'医疗器械生产许可证',),
    'device_registration': (r'医疗器械注册证',),
    'device_operation_filing': (r'第[二2Ⅱ]类医疗器械经营备案(?:凭证)?',),
    'device_production_filing': (r'第[一1Ⅰ]类医疗器械生产备案(?:凭证)?',),
    'device_product_filing': (r'第[一1Ⅰ]类医疗器械(?:产品)?备案(?:凭证|信息表)',),
    'enterprise_authorization': (r'授权书', r'LETTEROFAUTHORIZATION', r'AUTHORIZATIONLETTER'),
    'disinfection_hygiene_license': (r'消毒产品生产企业卫生许可证',),
    'medical_practice_license': (r'医疗机构执业许可证',),
    'quality_management_certificate': (r'质量管理体系认证证书',),
    'drug_operation_license': (r'药品经营许可证',),
}

NUMBER_PATTERNS = {
    'device_registration': r'(?:国|[京津沪渝冀晋辽吉黑苏浙皖闽赣鲁豫鄂湘粤琼川贵云陕甘青蒙桂藏宁新])械注[准进许][0-9]{8,12}',
    'device_operation_license': r'(?:食药监|药监)?械经营许[0-9A-Z]{6,}',
    'device_production_license': r'(?:食药监|药监)?械生产许[0-9A-Z]{6,}',
    'device_operation_filing': r'械经营备[0-9A-Z]{6,}',
    'device_production_filing': r'械生产备[0-9A-Z]{6,}',
    'device_product_filing': r'械备[0-9]{8,12}',
    'disinfection_hygiene_license': r'卫消证字',
    'quality_management_certificate': r'ISO(?:9001|13485)',
}


def _compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text))).upper()


def _box(entry):
    box = entry.get('box')
    if box is not None and len(box) == 4:
        return [float(v) for v in box]
    points = entry.get('polygon')
    if points is None:
        return None
    try:
        return [min(float(p[0]) for p in points), min(float(p[1]) for p in points),
                max(float(p[0]) for p in points), max(float(p[1]) for p in points)]
    except (TypeError, ValueError):
        return None


def _angle(entry):
    points = entry.get('polygon')
    if points is None or len(points) < 2:
        return 0.0
    try:
        dx, dy = float(points[1][0])-float(points[0][0]), float(points[1][1])-float(points[0][1])
        return abs(math.degrees(math.atan2(dy, dx))) % 180
    except (TypeError, ValueError):
        return 0.0


def _candidate(entry):
    return {'text': _compact(entry.get('text', '')), 'raw_text': entry.get('text', ''),
            'box': _box(entry), 'ids': [entry.get('id')], 'source': 'single_box',
            'ocr_score': float(entry.get('score', 0)), 'angle': _angle(entry)}


def _head_candidates(entries, image_size):
    width, height = image_size
    valid = []
    for entry in entries:
        item = _candidate(entry)
        if not item['text'] or not item['box']:
            continue
        box = item['box']
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        valid.append(item)
    unit = median([v['box'][3]-v['box'][1] for v in valid]) if valid else 1.0
    heads = [v for v in valid if v['box'][1] < height*.52 and len(v['text']) <= 72
             and min(v['angle'], 180-v['angle']) < 35]
    candidates = list(heads)
    # Join adjacent fragments on the same physical title row; no filename or answer text.
    for first in sorted(heads, key=lambda v:(v['box'][1], v['box'][0])):
        run = [first]
        fh = first['box'][3]-first['box'][1]
        fc = (first['box'][1]+first['box'][3])/2
        others = sorted((v for v in heads if v is not first and v['box'][0] >= first['box'][2]), key=lambda v:v['box'][0])
        for other in others:
            oh = other['box'][3]-other['box'][1]
            oc = (other['box'][1]+other['box'][3])/2
            if abs(fc-oc) > .48*min(fh,oh) or max(fh,oh) > 2.2*min(fh,oh):
                continue
            if other['box'][0]-run[-1]['box'][2] > 4.0*max(fh,oh):
                break
            run.append(other)
            candidates.append(_combine(run,'same_row'))
            if len(run) >= 8:
                break
    # Two-line headings are frequent: 医疗器械 above 质量管理体系认证证书.
    short_heads = [v for v in heads if len(v['text']) <= 30]
    for top in short_heads:
        a=top['box']; ah=a[3]-a[1]
        for bottom in short_heads:
            if top is bottom:
                continue
            b=bottom['box']; bh=b[3]-b[1]
            overlap=max(0,min(a[2],b[2])-max(a[0],b[0]))
            if a[3] <= b[1] <= a[3]+2.2*max(ah,bh) and overlap > .45*min(a[2]-a[0], b[2]-b[0]):
                candidates.append(_combine([top,bottom],'stacked_title'))
    return valid, candidates, max(unit,1.0)


def _combine(items, source):
    return {'text': ''.join(v['text'] for v in items), 'raw_text': ' '.join(v['raw_text'] for v in items),
            'box':[min(v['box'][0] for v in items), min(v['box'][1] for v in items),
                   max(v['box'][2] for v in items), max(v['box'][3] for v in items)],
            'ids':[sid for v in items for sid in v['ids']], 'source':source,
            'ocr_score': min(v['ocr_score'] for v in items), 'angle':0.0}


def _title_strength(candidate, pattern, unit, height):
    match = re.search(pattern, candidate['text'])
    if not match:
        return None
    before, after = candidate['text'][:match.start()], candidate['text'][match.end():]
    # A title may be prefixed with a jurisdiction or 国家/中华人民共和国 and suffixed with 正/副本.
    # Narrative references such as 提交营业执照/原医疗器械注册证 are not a title.
    prefix_ok = not before or bool(re.fullmatch(r'(?:中华人民共和国|中国|[\u4e00-\u9fff]{2,8}(?:省|市|自治区))', before))
    suffix_ok = not after or bool(re.fullmatch(r'[（(]?(?:正本|副本|正|副|体外诊断试剂)[）)]?', after))
    if not prefix_ok or not suffix_ok:
        return None
    box=candidate['box']; ch=box[3]-box[1]
    top=box[1]/max(height,1)
    if ch < unit*1.1 and top > .32:
        return None
    # Standalone exact heading is strong; generous head region permits framed photos.
    strength=.79 + (.08 if ch >= 1.5*unit else .025) + (.055 if top < .35 else 0)
    if candidate['source'] != 'single_box':
        strength-=.025
    return min(.94,strength)


def classify_document(entries, image_size):
    """Route OCR entries by title evidence, or abstain when unknown/conflicting.

    `entries` are ordinary OCR text/polygon dictionaries; `image_size` is (width,height).
    `score` is a deterministic rule score and is not calibrated confidence.
    """
    try:
        width,height=map(float,image_size)
    except (TypeError, ValueError):
        width,height=0.0,0.0
    unknown={'type':'unknown','label':'未确定证照类型','status':'unknown','score':0.0,
             'evidence':[],'candidates':[],'supported':False}
    if width <= 0 or height <= 0:
        return unknown
    valid,heads,unit=_head_candidates(entries,(width,height))
    matches={}
    for key,patterns in TITLE_PATTERNS.items():
        for item in heads:
            for pattern in patterns:
                strength=_title_strength(item,pattern,unit,height)
                if strength is None:
                    continue
                evidence=[{'kind':'title','text':item['raw_text'],'bbox':item['box'],
                           'source_ids':item['ids'],'assembly':item['source'],'ocr_score':item['ocr_score']}]
                number_pattern=NUMBER_PATTERNS.get(key)
                supporting=[v for v in valid if number_pattern and re.search(number_pattern,v['text'])]
                if supporting:
                    strength=min(.99,strength+.055)
                    evidence.append({'kind':'format_support','text':supporting[0]['raw_text'],
                                     'bbox':supporting[0]['box'],'source_ids':supporting[0]['ids']})
                candidate={'type':key,'label':TYPE_LABELS[key],'score':round(strength,4),'evidence':evidence,'supported':key in PROFILES}
                if key not in matches or candidate['score'] > matches[key]['score']:
                    matches[key]=candidate
    ranked=sorted(matches.values(), key=lambda v:(-v['score'],v['type']))
    if not ranked:
        return unknown
    # Different complete headings often mean several documents in one photograph.
    # Do not force such a page through one parser even when one heading is larger.
    if len(ranked)>1 and ranked[1]['score'] >= .78:
        return {'type':'unknown','label':'证照类型存在冲突','status':'ambiguous',
                'score':ranked[0]['score'],'evidence':ranked[0]['evidence']+ranked[1]['evidence'],
                'candidates':ranked,'supported':False}
    best=ranked[0]
    return {**best,'status':'recognized','candidates':ranked}
