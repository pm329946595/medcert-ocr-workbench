"""Propose a small number of OCR evidence crops from labels and page geometry.

No image pixels, filenames, document answers or absolute template coordinates are
used here. Proposals are hypotheses, never extracted values. The caller must keep
conflicting crop OCR as candidates and must not replace existing value evidence.
"""
import re
import statistics
import unicodedata

from .schema import PROFILES


def _text(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))


def _box(entry):
    box = entry.get('box')
    if not box or len(box) != 4:
        polygon = entry.get('polygon', [])
        if not polygon:
            return None
        box = [min(p[0] for p in polygon), min(p[1] for p in polygon),
               max(p[0] for p in polygon), max(p[1] for p in polygon)]
    box = list(map(float, box))
    return box if box[2] > box[0] and box[3] > box[1] else None


def propose_regions(entries, image_size, profile, missing_keys, grid_summary=None):
    """Return at most two original-image crops for missing/partial field labels.

    ``missing_keys`` must be determined by the caller from its own extraction;
    existing complete fields are never proactively re-OCRed. Grid summaries use
    ``TableGeometry.as_dict()``. Conservative gates intentionally leave uncertain
    pages without a proposal. Soft runtime budgets belong to the OCR caller.
    """
    spec = PROFILES.get(profile) if isinstance(profile, str) else profile
    if spec is None or not missing_keys or not entries:
        return []
    width, height = map(float, image_size)
    if width <= 0 or height <= 0:
        return []
    fields = {field.key: field for field in spec.fields}
    missing = set(missing_keys) & fields.keys()
    # Number expansion is handled separately by the existing field recognizer.
    missing = {key for key in missing if fields[key].kind not in
               ('uscc', 'credit_code', 'registration_number', 'license_number')}
    if not missing:
        return []
    clean = []
    for entry in entries:
        box = _box(entry)
        if box is None or box[3]-box[1] > height*.12:
            continue
        polygon = entry.get('polygon', [])
        if len(polygon) >= 2 and abs(polygon[1][1]-polygon[0][1]) > max(1, abs(polygon[1][0]-polygon[0][0]))*.5:
            continue
        clean.append({'text': _text(entry.get('text')), 'box': box, 'id': entry.get('id')})
    if not clean:
        return []
    unit = statistics.median(item['box'][3]-item['box'][1] for item in clean)
    aliases = [(key, _text(alias)) for key, field in fields.items() for alias in field.aliases]
    aliases += [(None, _text(alias)) for alias in spec.boundary_labels]
    aliases.sort(key=lambda item: len(item[1]), reverse=True)
    anchors = []
    for item in clean:
        value = item['text']
        for key, alias in aliases:
            if len(alias) < 2 or not value.startswith(alias):
                continue
            rest = value[len(alias):]
            if rest and not rest.startswith((':', '：')):
                continue
            x1, y1, x2, y2 = item['box']
            label_right = x1 + (x2-x1)*len(alias)/max(1, len(value)) if rest else x2
            anchors.append({'key': key, 'box': [x1, y1, label_right, y2],
                            'entry_box': item['box'], 'source_text': value, 'partial': False})
            break
    # A page needs several real label anchors before a stray character is a
    # plausible partial label or blank band can be interpreted structurally.
    if len(anchors) < 3:
        return []
    candidates = []

    def offer(box, reason, expected, priority, evidence):
        x1, y1, x2, y2 = box
        clipped = [max(0, int(x1)), max(0, int(y1)), min(int(width), int(x2+1)), min(int(height), int(y2+1))]
        area = max(0, clipped[2]-clipped[0])*max(0, clipped[3]-clipped[1])
        if clipped[2]-clipped[0] < unit or clipped[3]-clipped[1] < unit or area > width*height*.18:
            return
        candidates.append({'box': clipped, 'reason': reason, 'expected_fields': sorted(set(expected) & missing),
                           'priority': priority, 'evidence': evidence})

    # Recover spaced two-character labels from their peer label column; the
    # actual missing glyph is not invented and the crop contains no field value.
    partials = []
    for item in clean:
        token = item['text'].strip(':：')
        if len(token) != 1:
            continue
        possible = {key for key, alias in aliases if key in fields and len(alias) == 2 and token in alias
                    and alias in ('名称', '住所', '类型', '附件', '备注')}
        if len(possible) != 1:
            continue
        key = next(iter(possible))
        box = item['box']
        peers = [a for a in anchors if abs(a['box'][2]-box[2]) <= unit*3 and
                 abs((a['box'][1]+a['box'][3]-box[1]-box[3])/2) < height*.30]
        if len(peers) < 2:
            continue
        left = statistics.median(a['box'][0] for a in peers)
        right = statistics.median(a['box'][2] for a in peers)
        if left > box[0] or box[0]-left > unit*7:
            continue
        anchor = {'key': key, 'box': [left, box[1], max(right, box[2]), box[3]],
                  'entry_box': box, 'partial': True}
        partials.append(anchor)
        if key in missing:
            offer([left-unit*.55, box[1]-unit*.65, max(right, box[2])+unit*.4, box[3]+unit*.65],
                  'partial_label_on_supported_track', [key], .90,
                  {'partial_text': token, 'peer_labels': len(peers)})
    all_anchors = anchors+partials

    # Find an empty label cell on an established ruled-table label track. Its
    # adjacent value text is already available and does not need re-recognition.
    grid = grid_summary or {}
    horizontal = grid.get('horizontal_lines', [])
    vertical = grid.get('vertical_lines', [])
    order = [field.key for field in spec.fields]
    if spec.table_preferred and len(horizontal) >= 3 and len(vertical) >= 3:
        for left, divider in zip(vertical, vertical[1:]):
            lx, dx = left['position'], divider['position']
            track = [a for a in all_anchors if lx-unit*.2 <= a['box'][0] and a['box'][2] <= dx+unit*.2]
            if len(track) < 3 or dx-lx > width*.4:
                continue
            borders = [h['position'] for h in horizontal if h['start'] <= lx+unit*.3 and h['end'] >= dx+unit]
            for top, bottom in zip(borders, borders[1:]):
                if bottom-top < unit or bottom-top > height*.35:
                    continue
                if any(top <= (a['box'][1]+a['box'][3])/2 <= bottom for a in track):
                    continue
                before = [a for a in track if a['box'][3] <= top+unit*.25 and a['key'] in order]
                after = [a for a in track if a['box'][1] >= bottom-unit*.25 and a['key'] in order]
                if not before or not after:
                    continue
                prev = max(before, key=lambda a: a['box'][3])['key']
                nxt = min(after, key=lambda a: a['box'][1])['key']
                start, end = order.index(prev), order.index(nxt)
                expected = [key for key in order[start+1:end] if key in missing]
                if len(expected) != 1 or not end > start:
                    continue
                offer([lx-unit*.25, top-unit*.22, dx+unit*.20, bottom+unit*.22],
                      'empty_label_cell_between_known_fields', expected, .98,
                      {'neighbor_fields': [prev, nxt], 'physical_cell': [lx, top, dx, bottom]})

    # Same label track, one omitted row between nearby known fields. Restrict to
    # one plausible schema field and a gap close to two normal row pitches.
    if not spec.table_preferred:
        tracks = []
        for anchor in sorted(all_anchors, key=lambda a: a['box'][0]):
            for track in tracks:
                if abs(anchor['box'][0]-statistics.median(a['box'][0] for a in track)) <= unit*1.8:
                    track.append(anchor)
                    break
            else:
                tracks.append([anchor])
        for track in tracks:
            if len(track) < 3:
                continue
            track.sort(key=lambda a: a['box'][1])
            gaps = [(b['box'][1]+b['box'][3]-a['box'][1]-a['box'][3])/2 for a, b in zip(track, track[1:])]
            positive = sorted(g for g in gaps if g > unit*1.2)
            if len(positive) < 2:
                continue
            pitch = statistics.median(positive[:max(2, (len(positive)+1)//2)])
            if pitch < unit*1.5:
                continue
            for a, b, gap in zip(track, track[1:], gaps):
                if not 1.6*pitch <= gap <= 2.6*pitch or a['key'] not in order or b['key'] not in order:
                    continue
                ia, ib = order.index(a['key']), order.index(b['key'])
                expected = [key for key in order[ia+1:ib] if key in missing]
                if len(expected) != 1 or ib <= ia:
                    continue
                top, bottom = a['box'][3]+pitch*.17, b['box'][1]-pitch*.12
                right = max(e['box'][2] for e in clean if e['box'][1] > height*.3)
                offer([min(a['box'][0], b['box'][0])-unit*.7, top, min(width, right+unit*.7), bottom],
                      'one_missing_row_between_label_tracks', expected, .95,
                      {'neighbor_fields': [a['key'], b['key']], 'row_pitch': round(pitch, 2)})
            # A date interval may print its end on the next line of this column.
            start_fields = [a for a in track if a['key'] in fields and fields[a['key']].kind == 'date_start'
                            and '至' not in a.get('source_text', '')]
            end_keys = [key for key in missing if fields[key].kind == 'date_end']
            if start_fields and len(end_keys) == 1:
                a = start_fields[-1]
                other_columns = [b['box'][0] for b in all_anchors if b['box'][0] > a['box'][2]+unit*3]
                right = min(other_columns)-unit*.7 if other_columns else max(e['box'][2] for e in clean)
                cy = (a['box'][1]+a['box'][3])/2+pitch
                if cy+unit < height and right-a['box'][0] > unit*5:
                    offer([a['box'][0]-unit*.7, cy-unit*1.0, right, cy+unit*1.0],
                          'missing_interval_end_below_start', end_keys, .93,
                          {'start_field': a['key'], 'row_pitch': round(pitch, 2)})
    chosen = []
    for candidate in sorted(candidates, key=lambda row: row['priority'], reverse=True):
        if not candidate['expected_fields']:
            continue
        if any(set(candidate['expected_fields']) & set(old['expected_fields']) for old in chosen):
            continue
        chosen.append(candidate)
        if len(chosen) == 2:
            break
    return chosen
