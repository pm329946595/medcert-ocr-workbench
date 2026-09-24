"""Shared, evidence-preserving label/geometry parser for local credentials.

No filename, corpus annotation, fixed page coordinate or external service is used.
The original OCR always remains available; uncertainty requires human review.
"""
import re
import time
import unicodedata
from datetime import date
from statistics import median

from layout import center, height, union
from .validators import credit_valid

NOISE = re.compile(r'二维码|扫码|公示系统|年度报告|专用章|https?://|www\.|仅供.{0,12}(展示|使用)|他用无效|SCJDGL', re.I)
CN_DIGITS = dict(zip('零〇○一二三四五六七八九', '000123456789'))


def compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))


def _number(value):
    if value.isdigit():
        return int(value)
    if '十' in value:
        left, right = value.split('十', 1)
        return (int(CN_DIGITS.get(left, '1')) if left else 1) * 10 + (int(CN_DIGITS.get(right, '0')) if right else 0)
    return int(''.join(CN_DIGITS.get(c, c) for c in value))


def dates_in(text):
    text = compact(text)
    pattern = r'([0-9零〇○一二三四五六七八九]{4})[年./-]([0-9零〇○一二三四五六七八九十]{1,3})[月./-]([0-9零〇○一二三四五六七八九十]{1,3})日?'
    results = []
    for match in re.finditer(pattern, text):
        try:
            results.append({'value': date(*(_number(v) for v in match.groups())).isoformat(), 'span': list(match.span()), 'raw': match.group()})
        except (ValueError, TypeError):
            continue
    return results


def _normal_map(text):
    value, mapping = [], []
    for i, char in enumerate(text):
        for c in unicodedata.normalize('NFKC', char):
            if not c.isspace():
                value.append(c)
                mapping.append(i)
    return ''.join(value), mapping


def _span_box(entry, start, end):
    # This is only an association estimate. Evidence keeps the full original polygon.
    x1, y1, x2, y2 = entry['box']
    total = max(1, len(entry['text']))
    return [x1 + (x2-x1)*start/total, y1, x1 + (x2-x1)*end/total, y2]


def _piece(entry, start, end):
    source_id=entry.get('source_id',entry['id'])
    offset=entry.get('value_span',[0,0])[0]
    return dict(entry, id=f"{source_id}@{offset+start}:{offset+end}", source_id=source_id,
                source_text=entry.get('source_text',entry['text']), source_box=entry.get('source_box',entry['box']),
                value_span=[offset+start, offset+end],
                box=_span_box(entry, start, end), text=entry['text'][start:end].strip(' :：'))


def locate_anchors(entries, spec):
    aliases = [(compact(a), f.key) for f in spec.fields for a in f.aliases]
    aliases += [(compact(a), '_boundary_'+str(i)) for i, a in enumerate(spec.boundary_labels)]
    aliases = sorted(set(aliases), key=lambda pair: -len(pair[0]))
    anchors, fragments, consumed = [], [], set()
    for e in entries:
        text, positions = _normal_map(e['text'])
        found = []
        for alias, key in aliases:
            for match in re.finditer(re.escape(alias), text):
                start, end = match.span()
                # Interior labels need a colon; an old certificate number in remarks
                # is not the primary number. Schema boundary words cannot split prose.
                if start and (end >= len(text) or text[end] != ':' or key.startswith('_boundary')):
                    continue
                if start and (text[max(0, start-1):start] == '原' or alias in ('名称', '住所', '至')):
                    continue
                if len(alias) == 1 and text[end:] and not re.match(r'[:自至0-9二〇一三四五六七八九十年]', text[end:]):
                    continue
                if any(start < b and end > a for a,b,_,_ in found):
                    continue
                found.append((start, end, alias, key))
        if not found:
            continue
        found.sort()
        consumed.add(e['id'])
        if found[0][0]>0:
            # A split label tail can precede another complete inline label.
            # Keep that prefix as a separately traceable fragment for the join.
            fragments.append(_piece(e,0,positions[found[0][0]]))
        for index, (start, end, alias, key) in enumerate(found):
            raw_start, raw_end = positions[start], positions[end-1]+1
            stop = positions[found[index+1][0]] if index+1 < len(found) else len(e['text'])
            tail_start = raw_end
            while tail_start < stop and (e['text'][tail_start].isspace() or e['text'][tail_start] in ':：'):
                tail_start += 1
            tail = _piece(e, tail_start, stop) if tail_start < stop else None
            label_box = _span_box(e, raw_start, raw_end)
            anchor = dict(e, box=label_box, field=key, alias=alias, ids=[e['id']],
                          anchor_id=f"{e['id']}:{start}:{key}", inline=tail,
                          association=1.0, label_span=[raw_start, raw_end])
            anchors.append(anchor)
            if tail and tail['text']:
                tail['inline_anchor'] = anchor['anchor_id']
                fragments.append(tail)
    # Separate short label fragments, including 名 + 称<value>.
    for first in sorted(entries, key=lambda e: (center(e), e['box'][0])):
        if first['id'] in consumed:
            continue
        prefix = compact(first['text']).rstrip(':')
        if not prefix or len(prefix) > 4:
            continue
        options = [(a,k) for a,k in aliases if a.startswith(prefix) and a != prefix]
        if not options:
            continue
        for other in sorted(entries+fragments[:], key=lambda e: e['box'][0]):
            if other['id'] in consumed or other['id'] == first['id']:
                continue
            gap = other['box'][0] - first['box'][2]
            if gap < -0.3*height(first) or gap > 7*height(first):
                continue
            if abs(center(first)-center(other)) > .65*max(height(first), height(other)):
                continue
            norm, positions = _normal_map(other['text'])
            found = next(((a,k) for a,k in options if norm.startswith(a[len(prefix):])), None)
            if not found:
                continue
            alias, key = found
            raw_end = positions[len(alias)-len(prefix)-1]+1
            tail_start = raw_end
            while tail_start < len(other['text']) and (other['text'][tail_start].isspace() or other['text'][tail_start] in ':：'):
                tail_start += 1
            tail = _piece(other, tail_start, len(other['text'])) if tail_start < len(other['text']) else None
            part = dict(other, box=_span_box(other, 0, raw_end))
            anchor = dict(first, box=union([first,part]), field=key, alias=alias,
                ids=[first['id'],other.get('source_id',other['id'])], anchor_id=f"{first['id']}+{other['id']}:{key}",
                inline=tail, association=.98)
            anchors.append(anchor)
            consumed.update([*anchor['ids'],other['id']])
            if tail and tail['text']:
                tail['inline_anchor'] = anchor['anchor_id']
                fragments.append(tail)
            break
    strong_keys={a['field'] for a in anchors}
    if len(strong_keys)>=3:
        for e in entries:
            if e['id'] in consumed:
                continue
            token=compact(e['text']).strip(':')
            if len(token)!=1:
                continue
            matches=[(a,k) for a,k in aliases if len(a)==2 and token in a and k not in strong_keys
                     and a in ('名称','住所','类型','附件','备注')]
            if len(matches)!=1:
                continue
            alias,key=matches[0]
            anchors.append(dict(e,field=key,alias=alias,ids=[e['id']],
                anchor_id=f"{e['id']}:partial:{key}",inline=None,association=.65,partial_label=True))
            consumed.add(e['id'])
    # OCR may retain only the last character of a spaced 名称 label.
    # Return visible company text with a review flag, never infer a company name.
    if len(strong_keys)>=3:
        key=next((f.key for f in spec.fields if f.key=='enterprise_name'),'')
        if key and key not in strong_keys:
            for e in entries:
                token,positions=_normal_map(e['text'])
                if e['id'] not in consumed and re.fullmatch(r'称[:：]?[\u4e00-\u9fffA-Za-z（）()·]{4,}(?:公司|企业|药房)',token):
                    tail_start=positions[1]
                    while tail_start<len(e['text']) and e['text'][tail_start] in ' :：':tail_start+=1
                    tail=_piece(e,tail_start,len(e['text']))
                    anchor=dict(e,box=_span_box(e,0,1),field=key,alias='名称',ids=[e['id']],
                        anchor_id=f"{e['id']}:partial_name",inline=tail,association=.65,partial_label=True)
                    tail['inline_anchor']=anchor['anchor_id'];anchors.append(anchor);fragments.append(tail);consumed.add(e['id'])
    fragments=[f for f in fragments if f['id'] not in consumed]
    fragments.extend(dict(e, source_id=e['id'], source_text=e['text'], source_box=e['box'],
                          value_span=[0,len(e['text'])]) for e in entries if e['id'] not in consumed)
    return anchors, fragments, consumed


def _rows(entries):
    rows = []
    for e in sorted(entries, key=lambda p: (center(p), p['box'][0])):
        row = next((r for r in reversed(rows[-3:]) if abs(center(e)-median(center(p) for p in r)) <= .45*min(height(e), median(height(p) for p in r))), None)
        if row is None:
            rows.append([e])
        else:
            row.append(e)
    return [sorted(row, key=lambda e:e['box'][0]) for row in rows]


def _join(entries):
    # Keep source spelling. No language-model correction or inserted punctuation.
    return ''.join(e['text'].strip() for row in _rows(entries) for e in row)


def _eligible(e, unit):
    if not e['text'].strip() or NOISE.search(e['text']):
        return False
    if height(e) > 3.2*unit or e['box'][2]-e['box'][0] < .25*height(e):
        return False
    poly = e.get('polygon', [])
    if len(poly) >= 2 and abs(poly[1][1]-poly[0][1]) > .35*max(1,abs(poly[1][0]-poly[0][0])):
        return False
    return True


def _address_tail_incomplete(field, text):
    # City/district/street-level endings do not establish a complete delivery
    # location. This is a review gate, not an instruction to invent a suffix.
    key=getattr(field, 'key', '')
    return (key=='address' or key.endswith('_address')) and bool(
        re.search(r'(?:省|市|县|区|镇|乡|街道|路)[。.;；]?$', compact(text)))


def collect_value(anchor, anchors, fragments, field, image_size, unit, grid, table_preferred):
    x1,y1,x2,y2 = anchor['box']
    pool = [e for e in fragments if _eligible(e,unit) and height(e)<=max(1.8*height(anchor),1.6*unit)
            and (not e.get('inline_anchor') or e['inline_anchor']==anchor['anchor_id'])]
    inline = anchor.get('inline')
    region = None
    issues = ['字段标签仅识别到部分字，归属需核对'] if anchor.get('partial_label') else []
    method = 'inline' if inline else 'right_row'
    if anchor.get('partial_label') and inline:
        return {'text':inline['text'],'entries':[inline],'issues':issues,'label_ids':anchor['ids'],
                'method':'partial_inline','association':anchor.get('association',.65),'anchor_alias':anchor['alias']}

    same_track = [a for a in anchors if a['anchor_id'] != anchor['anchor_id'] and abs(a['box'][0]-x1) < max(1.5*unit, (x2-x1)*.5)]
    previous = [a for a in same_track if center(a) < center(anchor)-.8*unit]
    following = [a for a in same_track if center(a) > center(anchor)+.8*unit]
    if grid is not None and not inline:
        region = grid.right_cell(anchor['box'])
    if region:
        l,t,r,b = region['box']
        chosen = [e for e in pool if l-.15*unit <= (e['box'][0]+e['box'][2])/2 <= r+.15*unit and t <= center(e) <= b and e['box'][0] >= l-.5*unit]
        method = 'table_cell'
        association = region.get('confidence',.9)
        # A box crossing a detected row divider may contain another field.
        if any(e['box'][1] < t-.35*height(e) or e['box'][3] > b+.35*height(e) for e in chosen):
            issues.append('文字框跨越表格行边界')
    elif table_preferred and not inline and previous and following:
        top = (center(max(previous,key=center))+center(anchor))/2
        bottom = (center(min(following,key=center))+center(anchor))/2
        chosen = [e for e in pool if e['box'][0] >= x2-.25*unit and top < center(e) < bottom]
        method, association = 'label_midpoints', .72
        issues.append('未确认表格边界，按相邻标签估计范围')
        region = {'box':[x2,top,image_size[0],bottom], 'confidence':association, 'method':method}
    else:
        right_labels = [a['box'][0] for a in anchors if a['box'][0] > x2+.7*unit and abs(center(a)-center(anchor)) < .8*max(height(a),height(anchor))]
        right = min(right_labels, default=image_size[0])
        bottom = min((a['box'][1] for a in following),default=image_size[1])
        top = max((a['box'][3] for a in previous),default=0)
        seeds = [e for e in pool if e['box'][0] >= x2-.55*unit and e['box'][2] <= right+.5*unit and e['box'][0]-x2 < 10*unit and abs(center(e)-center(anchor)) <= .8*max(height(e),height(anchor))]
        if inline and inline['text']:
            seeds = [inline] + [e for e in seeds if e['id']!=inline['id'] and e['box'][0]>=inline['box'][2]-(unit if field.kind.startswith('date') else .2*unit)]
            if not field.multiline and not field.kind.startswith('date'):
                seeds=[inline]
        if field.kind in ('uscc','credit_code'):
            seeds=[e for e in seeds if re.fullmatch(r'[A-Za-z0-9]{15,20}',compact(e['text']))]
        if not seeds and field.kind in ('uscc','credit_code'):
            seeds = [e for e in pool if .3*unit < center(e)-center(anchor) < 3*unit and abs(e['box'][0]-x1)<1.8*unit and e['box'][2] <= right]
            method = 'below_label'
        if not seeds:
            return {'text':'','entries':[],'issues':['标签附近未读到可靠值'],'label_ids':anchor['ids'],'method':method,'association':0}
        first = inline if inline and inline['text'] else min(seeds, key=(lambda e:e['box'][0]) if field.kind.startswith('date') else (lambda e:(abs(center(e)-center(anchor)), e['box'][0])))
        chosen = [first]
        for e in sorted(seeds,key=lambda p:p['box'][0]):
            if e['id']==first['id'] or e['box'][0]<first['box'][0]:
                continue
            if (abs(center(e)-center(first)) < .5*min(height(e),height(first))
                    and e['box'][0]-chosen[-1]['box'][2] < 2.5*unit):
                chosen.append(e)
        value_left = min(e['box'][0] for e in chosen)
        if field.multiline or field.kind in ('date_start','date_end','date_range'):
            neighbors = [e for e in pool if abs(e['box'][0]-value_left) < 1.8*unit and e['box'][2] <= right+.5*unit
                         and top < center(e) < bottom
                         and (center(e)>=center(first) or e['box'][3]>=y1-.15*height(e))
                         and (not inline or center(e)>center(first)+.4*height(first))]
            changed = True
            while changed:
                changed = False
                for e in neighbors:
                    if e in chosen:
                        continue
                    if any(abs(center(e)-center(c)) < 2.8*max(height(e),height(c)) for c in chosen):
                        if not any(abs(center(e)-center(c)) < 1.8*max(height(e),height(c)) for c in chosen):
                            issues.append('续行间距超过连续阈值，可能存在漏行')
                        chosen.append(e)
                        changed = True
        if field.multiline:
            leftovers=[e for e in neighbors if e not in chosen and center(e)>center(first)]
            if leftovers:
                issues.append('标签范围内仍有未归属的同列文字，字段可能不完整')
        association = anchor.get('association',1.0)
        region = {'box':[value_left,top,right,bottom], 'method':method}
    chosen = sorted(chosen, key=lambda e:(center(e),e['box'][0]))
    if chosen and not field.multiline and field.kind not in ('date_start','date_end','date_range') and len(_rows(chosen)) > 1:
        issues.append('单值字段出现多行候选，需核对归属')
    return {'text':_join(chosen),'entries':chosen,'issues':issues,'label_ids':anchor['ids'],
            'method':method,'association':association,'region':region,'anchor_alias':anchor['alias']}


def validate_value(field, candidate):
    text = candidate['text'].strip()
    value = text
    issues = list(candidate['issues'])
    kind = field.kind
    if not text:
        return '', issues + ['未获得可用 OCR 文字；不能据此判断原图为空']
    if _address_tail_incomplete(field,text):
        issues.append('地址止于行政区或道路名称，缺少完整地点信息，请核对是否漏行')
    if compact(text) in ('/', '\\', '-', '—', 'I', '|'):
        return '', issues + ['仅识别到划线或易混单字符，未获得可靠实际值']
    if getattr(field,'key','') in ('model_specification','structure_composition','intended_use','production_scope','business_scope') and re.search(r'(详?见|另见|参见).{0,10}(附[页件表]|登记表|技术要求)',text):
        issues.append('内容引用附页或其他文件，完整内容需补充核对')
    if kind in ('uscc','credit_code'):
        value = compact(text)
        if not credit_valid(value):
            issues.append('统一社会信用代码格式或校验位不通过')
    elif kind.startswith('date'):
        dates = dates_in(text)
        if len(dates)>2:
            return '', issues+['一个日期字段中出现超过两个日期，角色不明确']
        if kind == 'date_start':
            valid = dates[:1] if '自' in text or candidate.get('anchor_alias','').endswith('自') or len(dates)>=2 else []
        elif kind == 'date_end':
            valid = dates_in(text.rsplit('至',1)[1])[-1:] if '至' in text else dates[-1:] if len(dates)>=2 or not any(c in text for c in '自起') else []
        else:
            valid = dates if len(dates)==1 else []
        value = valid[0]['value'] if valid else ''
        if not value:
            issues.append('日期或日期角色不完整，不从其他日期推断')
    elif kind in ('registration_number', 'registration'):
        value = compact(text)
        if not re.fullmatch(r'[\u4e00-\u9fff]{1,4}械注[准进許许]\d{11}',value):
            issues.append('注册证号形式不完整或不属于已支持格式')
    elif kind == 'license_number':
        value = compact(text)
        if not re.fullmatch(r'[\u4e00-\u9fff]+(?:生产|经营)许\d{4}[A-Za-z0-9]+号?',value):
            issues.append('许可证编号形式异常或不完整')
    elif kind in ('person','person_name'):
        if not re.fullmatch(r'[\u4e00-\u9fff·•A-Za-z .-]{2,40}',text) or len(text)>12:
            issues.append('姓名字符或长度异常')
    elif kind in ('name','company','enterprise_name'):
        if len(compact(text))<4 or not re.search(r'公司|企业|中心|医院|诊所|药房|经营部|工厂|合作社|商店|集团|Corporation|Limited|Ltd',text,re.I):
            issues.append('企业名称未呈现完整组织名称')
    elif kind in ('authority','issuing_authority'):
        if not re.search(r'(局|委员会|管理处|管理所)$',text):
            issues.append('机关名称不完整，不能以印章碎片代替')
    if NOISE.search(text):
        issues.append('候选中含说明、水印或印章文字')
    return value, issues


def _recheck_truncated_number(field, chosen, visible, image_size, reread):
    """Recover a clipped final glyph only from agreeing expanded OCR crops.

    Existing characters cannot be replaced. An old number elsewhere in the page
    is never used, and a valid source number does not incur additional inference.
    """
    original = compact(chosen['text'])
    if (field.kind != 'registration_number' or reread is None or len(chosen['entries'])!=1
            or not re.fullmatch(r'[\u4e00-\u9fff]{1,4}械注[准进許许]\d{10}',original)):
        return None
    fragment=chosen['entries'][0]
    source=next((e for e in visible if e['id']==fragment.get('source_id')),None)
    if source is None:
        return None
    x1,y1,x2,y2=source['box']
    h=height(source)
    attempts=[]
    for margin in (.7,1.5):
        box=[max(0,x1-.15*h),max(0,y1-.15*h),min(image_size[0],x2+margin*h),min(image_size[1],y2+.15*h)]
        a,b,c,d=box
        expanded=dict(source,box=box,polygon=[[a,b],[c,b],[c,d],[a,d]])
        response=reread(expanded,False)
        norm=compact(response.get('text',''))
        number=next((norm[len(compact(alias)):].lstrip(':') for alias in sorted(field.aliases,key=len,reverse=True) if norm.startswith(compact(alias))),norm)
        attempts.append(dict(response,candidate=number,box=box,polygon=expanded['polygon']))
    accepted=(all(not a.get('error') and a.get('score',0)>=.98 for a in attempts)
              and attempts[0]['candidate']==attempts[1]['candidate']
              and attempts[0]['candidate'].startswith(original)
              and len(attempts[0]['candidate'])==len(original)+1
              and not validate_value(field,{'text':attempts[0]['candidate'],'issues':[]})[1])
    spatial_conflicts=[]
    for e in visible:
        if e['id']==source['id'] or abs(center(e)-center(source))>.8*max(height(e),h):
            continue
        a=e['box']
        expanded=attempts[-1]['box']
        intersection=max(0,min(expanded[2],a[2])-max(x2,a[0]))*max(0,min(expanded[3],a[3])-max(expanded[1],a[1]))
        if intersection/max(1,(a[2]-a[0])*(a[3]-a[1]))>.15:
            spatial_conflicts.append(e['id'])
    accepted=accepted and not spatial_conflicts
    return {'method':'expanded_number_consensus','accepted':accepted,'attempts':attempts,
            'source_id':source['id'],'original_candidate':chosen['text'],
            'spatial_conflicts':spatial_conflicts,
            'candidate':attempts[0]['candidate'] if accepted else chosen['text'],
            'policy':'两种局部扩框一致且仅增加缺失末位；已有字符不替换'}


def _merge_region_evidence(visible, records, spec):
    added=[]
    for record in records:
        aliases=[compact(a) for field in spec.fields if field.key in record.get('expected_fields',[]) for a in field.aliases]
        record['accepted_ids'],record['conflicts']=[],[]
        for item in record.get('entries',[]):
            text=compact(item['text']).strip(':')
            pure_label=any(text==a or (len(a)==2 and len(text)==1 and text in a) for a in aliases)
            if not text or item['score'] < (.85 if pure_label else .95):
                continue
            box=item['box']
            overlaps=[]
            for old in visible+added:
                a=old['box']
                intersection=max(0,min(box[2],a[2])-max(box[0],a[0]))*max(0,min(box[3],a[3])-max(box[1],a[1]))
                minimum=min((box[2]-box[0])*(box[3]-box[1]),(a[2]-a[0])*(a[3]-a[1]))
                if intersection/max(1,minimum)>.5:
                    overlaps.append(old)
            if any(compact(e['text']).strip(':')==text for e in overlaps):
                continue
            compatible_label=pure_label and all(any(compact(e['text']).strip(':') in a and len(compact(e['text']).strip(':'))<=len(a) for a in aliases) for e in overlaps)
            if overlaps and not compatible_label:
                record['conflicts'].append({'entry':item,'overlapping_ids':[e['id'] for e in overlaps],
                                            'decision':'保留为冲突候选，不替换既有文字'})
                continue
            added.append(dict(item,regional_recheck=True))
            record['accepted_ids'].append(item['id'])
    return added


def parse_document(entries, image_size, image=None, reread=None, document_type='auto', recover=None):
    from .schema import PROFILES, TYPE_LABELS
    from .classifier import classify_document
    start = time.perf_counter()
    visible = [e for e in entries if e['text'].strip()]
    classification = classify_document(visible,image_size)
    selected_type = classification.get('type','unknown') if document_type=='auto' else document_type
    result = {'document_type':selected_type,'document_label':TYPE_LABELS.get(selected_type,selected_type),
              'parser_version':'2.1','classification':classification,'image_size':list(image_size),
              'fields':{},'prefill':{},'review_required':[],'local_checks':{},
              'notes':['字段键为本地通用映射，请在接入其他系统时自行核对字段名。',
                       'source_bbox 和 polygon 保留原始整框；value_span 是 OCR 字符串索引，不是精确逐字坐标。',
                       '置信度不是准确率；已识别候选均回填，用户核对修改后自行提交。未识别到内容的字段才留空。',
                       '格式及校验位通过不等于证照真实性核验。']}
    if selected_type not in PROFILES:
        result.update(supported=False,document_status='已识别类型，暂未提供字段解析' if selected_type!='unknown' else '未确定证照类型，请选择类型或使用通用 OCR')
        return result
    if document_type!='auto' and classification.get('status')=='recognized' and classification.get('type')!=selected_type:
        result.update(supported=False,document_status='所选类型与图中文字标题不一致，未生成回填值')
        return result
    spec = PROFILES[selected_type]
    anchors, fragments, label_ids = locate_anchors(visible,spec)
    sparse_labels = len({a['field'] for a in anchors if not a['field'].startswith('_boundary')}) < 3
    unit = median(height(e) for e in visible) if visible else 1
    grid = None
    grid_start=time.perf_counter()
    if spec.table_preferred and image is not None:
        from .table_geometry import detect_grid
        grid = detect_grid(image,visible)
    result['geometry_seconds'] = round(time.perf_counter()-grid_start,4)
    if grid:
        result['table_geometry'] = grid.as_dict()
    if recover is not None:
        from .region_recovery import propose_regions
        complete_keys={a['field'] for a in anchors if not a.get('partial_label')}
        missing=[f.key for f in spec.fields if f.key not in complete_keys]
        regions=propose_regions(visible,image_size,spec,missing,grid.as_dict() if grid else None)
        records=recover(regions) if regions else []
        added=_merge_region_evidence(visible,records,spec)
        result['regional_recovery']=records
        result['regional_entries']=added
        if added:
            visible=visible+added
            anchors,fragments,label_ids=locate_anchors(visible,spec)
    risk=None
    if image is not None:
        from .visual_risk import ink_risk
        risk=ink_risk(image,visible)
        result['visual_risk']=risk.as_dict()
    fields, checks, selected_ids = {}, {}, set()
    for number, field in enumerate(spec.fields,1):
        candidates = [collect_value(a,anchors,fragments,field,image_size,unit,grid,spec.table_preferred) for a in anchors if a['field']==field.key]
        if not candidates and field.key=='valid_until':
            # A single validity range can contain both dates. Its role comes
            # from the explicit 至 marker, never by copying the issue date.
            range_candidates=[collect_value(a,anchors,fragments,field,image_size,unit,grid,spec.table_preferred)
                              for a in anchors if a['field']=='valid_from']
            candidates=[c for c in range_candidates if '至' in c['text'] and dates_in(c['text'])]
        # Some hygiene permits print a standalone statutory number with no label.
        if not candidates and selected_type=='disinfection_hygiene_license' and field.key=='license_number':
            for group in [[e] for e in visible]+_rows(visible):
                token=compact(''.join(e['text'] for e in group))
                if re.fullmatch(r'[\u4e00-\u9fff]{1,8}卫消证字[（(]\d{4}[）)][-－—]?(?:\d{1,4}[-－—])?第\d{3,8}号',token):
                    candidates.append({'text':''.join(e['text'] for e in group),'entries':group,'label_ids':[],
                        'issues':['无独立字段标签，按证号格式定位，请核对'],
                        'association':.9,'method':'standalone_number_format'})
        nonempty = [c for c in candidates if c['text']]
        chosen = max(nonempty,key=lambda c:c['association']) if nonempty else (candidates[0] if candidates else {'text':'','entries':[],'label_ids':[],'issues':['未定位到字段标签或内容'],'association':0,'method':'missing'})
        number_check = _recheck_truncated_number(field,chosen,visible,image_size,reread) if len(checks)<2 else None
        if number_check:
            checks['number:'+number_check['source_id']]=number_check
            if number_check['accepted']:
                chosen=dict(chosen,text=number_check['candidate'],method='expanded_number_consensus')
        value, issues = validate_value(field,chosen)
        if sparse_labels and chosen['text']:
            issues.append('有效字段标签较少，请核对字段归属')
        if any(record.get('conflicts') and field.key in record.get('expected_fields',[]) for record in result.get('regional_recovery',[])):
            issues.append('局部补读与既有文字有冲突，保留原候选供核对')
        if len({compact(validate_value(field,c)[0]) for c in nonempty if validate_value(field,c)[0]})>1:
            issues.append('同一字段发现多个不同候选')
        evidence=[]
        for e in chosen['entries']:
            source_id = e.get('source_id',e['id'])
            selected_ids.add(source_id)
            note = dict(e, box=e.get('source_box',e['box']))
            observation=risk.for_box(e['box']) if risk is not None else {'red_ink_ratio':0,'red_stamp_overlap':False,'watermark_overlap':False}
            red = observation['red_ink_ratio']
            red_overlap=observation['red_stamp_overlap']
            note['visual_risk']=observation
            note['red_ink_ratio'] = round(red,4)
            uncertain = e['score'] < .98
            if uncertain:
                issues.append('包含低置信度文字，请核对原图')
            if red_overlap:
                issues.append('文字区域与红色印迹重叠，请核对遮挡处')
            if observation['watermark_overlap']:
                issues.append('字段与已检测到的水印文字区域相交，请核对错漏字')
            if (uncertain or red_overlap) and reread is not None and len(checks)<3 and source_id not in checks:
                source = next((p for p in visible if p['id']==source_id),e)
                checks[source_id] = reread(source,red_overlap)
            if source_id in checks:
                check=checks[source_id]
                note['local_check']=check
                if not check.get('error') and compact(check.get('text',''))!=compact(e.get('source_text',e['text'])):
                    issues.append('局部复读与首轮文字不一致')
            if field.multiline and len(compact(chosen['text']))>60 and height(e)<14:
                issues.append('长文本字号较小，需核对错漏字与标点')
            evidence.append(note)
        if number_check and number_check['accepted']:
            for i, attempt in enumerate(number_check['attempts']):
                evidence.append({'id':f"number_recheck:{number_check['source_id']}:{i}",
                    'source_id':f"number_recheck:{number_check['source_id']}:{i}",
                    'text':attempt['candidate'],'source_text':attempt['text'],
                    'score':attempt['score'],'box':attempt['box'],'polygon':attempt['polygon'],
                    'source':'field_local','local_check':attempt})
        if any(e.get('seal_corrected') for e in chosen['entries']):
            issues.append('印章区域多路复读候选已更新，请对照原图核对')
        if field.multiline and len(_rows(chosen['entries']))>1:
            rows=_rows(chosen['entries'])
            gaps=[median(center(e) for e in b)-median(center(e) for e in a) for a,b in zip(rows,rows[1:])]
            if gaps and max(gaps)>2.4*median(height(e) for e in chosen['entries']):
                issues.append('多行内容存在较大间隙，可能漏行')
        if any(a['score']<.9 for a in anchors if a['field']==field.key):
            issues.append('字段标签识别置信度偏低')
        issues=list(dict.fromkeys(issues))
        status='已提取' if value and not issues else ('待核对' if chosen['text'] else '未识别')
        minimum=min((e['score'] for e in chosen['entries']),default=None)
        fields[field.key]={'number':number,'label':field.label,'value':value if status=='已提取' else '',
            'candidate':chosen['text'],'normalized_candidate':value,'status':status,'reasons':issues,
            'confidence':round(minimum*chosen['association']*(.7 if issues else 1),4) if minimum is not None else None,
            'ocr_confidence_min':minimum,'association_score':chosen['association'],
            'method':chosen['method'],'source_bbox':union(evidence) if evidence else None,
            'label_entry_ids':chosen['label_ids'],'evidence':evidence,
            'raw_text':'\n'.join(dict.fromkeys(e.get('source_text',e['text']) for e in chosen['entries'])),
            'alternatives':list(dict.fromkeys([c['text'] for c in nonempty if c is not chosen]+[v['text'] for e in chosen['entries'] for v in e.get('seal_candidates',[]) if v.get('text')])),
            'local_rechecked':any('local_check' in e or e.get('regional_recheck') for e in evidence)
                 or any(e.get('regional_recheck') and e['id'] in chosen['label_ids'] for e in visible), 'region':chosen.get('region')}
        if number_check:
            fields[field.key]['number_boundary_check']=number_check
            if number_check.get('spatial_conflicts'):
                fields[field.key]['reasons'].append('编号扩框与其他文字存在竞争归属，不采纳扩框结果')
        if any('内容引用附页' in reason for reason in issues):
            fields[field.key]['attachment_required']=True
    for begin,end in (('valid_from','valid_until'),('approval_date','valid_until'),('effective_date','valid_until')):
        if begin not in fields or end not in fields:
            continue
        left,right=fields[begin].get('normalized_candidate'),fields[end].get('normalized_candidate')
        if left and right and re.fullmatch(r'\d{4}-\d{2}-\d{2}',left) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',right) and left>right:
            for key in (begin,end):
                fields[key]['value']=''
                fields[key]['status']='待核对'
                fields[key]['reasons'].append('起始或批准日期晚于有效期截止日期，日期关系冲突')
    # Distinct character spans of a shared source box can legitimately belong to
    # different fields. Only overlapping spans are conflicts (date roles excluded).
    ownership={}
    for key,f in fields.items():
        for e in f['evidence']:
            ownership.setdefault(e.get('source_id',e['id']),[]).append((key,e.get('value_span',[0,len(e['text'])])))
    for owners in ownership.values():
        for i,(key,span) in enumerate(owners):
            for other,other_span in owners[i+1:]:
                if key==other or {key,other}<= {'valid_from','valid_until'}:
                    continue
                if max(span[0],other_span[0]) < min(span[1],other_span[1]):
                    for target in (key,other):
                        fields[target]['value']=''
                        fields[target]['status']='待核对'
                        fields[target]['reasons']=list(dict.fromkeys(fields[target]['reasons']+['同一文字片段被多个字段引用，归属有冲突']))
    for field in fields.values():
        # Review status never discards recognized content; preserve raw fallback for invalid dates.
        field['value'] = field.get('normalized_candidate') or field.get('candidate') or ''
        minimum=min((e['score'] for e in field['evidence']),default=None)
        field['confidence']=round(minimum*field['association_score']*(1 if field['status']=='已提取' else .7),4) if minimum is not None else None
    result.update(supported=True,document_status='已定位字段；请结合证据核对',fields=fields,
        prefill={k:f['value'] for k,f in fields.items()},review_required=[k for k,f in fields.items() if f['status']!='已提取'],
        local_checks=checks,unassigned_entry_ids=[e['id'] for e in visible if e['id'] not in selected_ids and e['id'] not in label_ids],
        parse_seconds=round(time.perf_counter()-start,4))
    return result
