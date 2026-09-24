"""Local evidence artifacts separate from extraction decisions."""
from PIL import ImageDraw, ImageFont
from layout import union
import re


def _box(item):
    box = item.get('box')
    if isinstance(box, (list, tuple)) and len(box) == 4:
        return list(box)
    points = item.get('polygon') or []
    if points:
        return [min(p[0] for p in points), min(p[1] for p in points),
                max(p[0] for p in points), max(p[1] for p in points)]
    return None


def save_evidence(original, entries, structured, out):
    picture = original.copy()
    draw = ImageDraw.Draw(picture)
    font = ImageFont.truetype('msyh.ttc', 24)
    index = {e['id']: e for e in entries}
    folder = out / 'fields'
    folder.mkdir(exist_ok=True)
    for number, (key, field) in enumerate((structured.get('fields') or {}).items(), 1):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            continue
        evidence = [dict(e, box=_box(e)) for e in field.get('evidence', []) if _box(e)]
        labels = [index[i] for i in field.get('label_entry_ids', []) if i in index]
        source_box = field.get('source_bbox')
        if not evidence and isinstance(source_box, (list, tuple)) and len(source_box) == 4:
            evidence = [{'box': list(source_box)}]
        if not evidence and not labels:
            continue
        selected = evidence + labels
        box = union(selected)
        margin = max(8, (box[3]-box[1]) * .12)
        region = (max(0, int(box[0]-margin)), max(0, int(box[1]-margin)),
                  min(original.width, int(box[2]+margin)), min(original.height, int(box[3]+margin)))
        if region[2] <= region[0] or region[3] <= region[1]:
            continue
        original.crop(region).save(folder / f'{key}.png')
        color = '#007e78' if field.get('status') == '已提取' else '#c26400'
        for entry in evidence:
            x1, y1, x2, y2 = entry['box']
            points = entry.get('polygon') or [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
            polygon = [tuple(map(int, point)) for point in points]
            draw.line(polygon + [polygon[0]], fill=color, width=4)
        draw.text((int(box[0]), max(0, int(box[1])-34)), f"{field.get('number', number)} {field.get('label', key)}", font=font, fill=color)
    picture.save(out / 'license_fields.png')


def table_rows(structured):
    def score(value):
        return round(value, 4) if isinstance(value, (int, float)) else None
    return [[f.get('number', number), f.get('label', key), f.get('value', ''),
             f.get('candidate', ''), f.get('status', '未识别'),
             score(f.get('ocr_confidence_min')), score(f.get('confidence')),
             '；'.join(f.get('reasons') or [])]
            for number, (key, f) in enumerate((structured.get('fields') or {}).items(), 1)]
