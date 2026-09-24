"""Conservative geometry-only reading order. Never correct or invent characters."""
from statistics import median
import re


def bounds(polygon):
    return [min(p[0] for p in polygon), min(p[1] for p in polygon),
            max(p[0] for p in polygon), max(p[1] for p in polygon)]


def union(items):
    boxes = [item['box'] for item in items]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def height(item):
    return max(1, item['box'][3] - item['box'][1])


def center(item):
    return (item['box'][1] + item['box'][3]) / 2


def entries_from_result(raw, source='primary', offset=(0, 0)):
    entries = []
    for page_index, page in enumerate(raw):
        data = page.get('res', page)
        for index, (text, score, polygon) in enumerate(zip(data.get('rec_texts', []),
                data.get('rec_scores', []), data.get('rec_polys', []))):
            poly = [[float(p[0]) + offset[0], float(p[1]) + offset[1]] for p in polygon]
            entries.append({'id': f'{source}:{page_index}:{index}', 'text': text,
                            'score': float(score), 'polygon': poly, 'box': bounds(poly), 'source': source})
    return entries


def _join(a, b, same_line=False):
    # Spaces between detected boxes preserve boundaries in identifiers and labels.
    if same_line or (a and b and a[-1].isascii() and b[0].isascii()):
        return a + ' ' + b
    return a + b


def _gaps(items, axis):
    intervals = sorted((p['box'][axis], p['box'][axis + 2]) for p in items)
    end = intervals[0][1]
    gaps = []
    for lo, hi in intervals[1:]:
        if lo > end:
            gaps.append((lo - end, (lo + end) / 2))
        end = max(end, hi)
    return gaps


def reading_order(items, unit):
    if len(items) < 2:
        return items
    top = min(p['box'][1] for p in items)
    bottom = max(p['box'][3] for p in items)
    headings = [p for p in items if height(p) > 2.5 * unit and
                p['box'][2] - p['box'][0] > 3 * height(p) and p['box'][1] < top + .25 * (bottom - top)]
    if headings:
        boundary = max(p['box'][3] for p in headings) + 2.5 * unit
        rest = [p for p in items if p not in headings]
        header = [p for p in rest if center(p) <= boundary]
        body = [p for p in rest if center(p) > boundary]
        return sorted(headings, key=lambda p: p['box'][1]) + reading_order(header, unit) + reading_order(body, unit)
    # Large horizontal whitespace defines sections; vertical whitespace defines columns.
    for axis, threshold in ((1, unit * .9), (0, unit * 1.5)):
        gaps = [(gap, cut) for gap, cut in _gaps(items, axis) if gap >= threshold]
        if gaps:
            _, cut = max(gaps)
            first = [p for p in items if p['box'][axis + 2] <= cut]
            second = [p for p in items if p['box'][axis] >= cut]
            if first and second and len(first) + len(second) == len(items):
                return reading_order(first, unit) + reading_order(second, unit)
    return sorted(items, key=lambda p: (p['box'][1], p['box'][0]))


def organize(entries):
    visible = [e for e in entries if e['text'].strip()]
    if not visible:
        return {'text': '', 'paragraphs': [], 'ordered_entries': [], 'empty_entries': [e['id'] for e in entries]}
    unit = median(height(e) for e in visible)
    rows = []
    for entry in sorted(visible, key=lambda e: (center(e), e['box'][0])):
        matches = [row for row in rows if abs(center(entry) - row['center']) <= .38 * min(height(entry), row['height'])
                   and max(height(entry), row['height']) <= 1.8 * min(height(entry), row['height'])]
        if matches:
            row = min(matches, key=lambda r: abs(r['center'] - center(entry)))
            row['entries'].append(entry)
            row['center'] = median(center(e) for e in row['entries'])
        else:
            rows.append({'entries': [entry], 'center': center(entry), 'height': height(entry)})
    lines = []
    for row in rows:
        runs = []
        for entry in sorted(row['entries'], key=lambda e: e['box'][0]):
            if runs and entry['box'][0] - runs[-1][-1]['box'][2] <= 2.8 * min(height(entry), height(runs[-1][-1])):
                runs[-1].append(entry)
            else:
                runs.append([entry])
        for run in runs:
            text = run[0]['text']
            for entry in run[1:]:
                text = _join(text, entry['text'], same_line=True)
            lines.append({'box': union(run), 'text': text, 'entries': run,
                          'anchor': run[-1]['box'][0] if len(run) > 1 else run[0]['box'][0],
                          'last_box': union(run)})
    paragraphs = []
    for line in sorted(lines, key=lambda p: (p['box'][1], p['box'][0])):
        candidates = []
        for paragraph in paragraphs:
            previous = paragraph['last_box']
            lh = height(line)
            ph = max(1, previous[3] - previous[1])
            delta = center(line) - (previous[1] + previous[3]) / 2
            gap = line['box'][1] - previous[3]
            aligned = abs(line['box'][0] - paragraph['anchor']) < .65 * min(lh, ph)
            long_previous = len(paragraph['last_text']) >= 5
            horizontal = previous[2] - previous[0] > 3 * ph
            if aligned and long_previous and horizontal and .45 * min(lh, ph) < delta < 1.5 * max(lh, ph) and gap < .5 * min(lh, ph):
                candidates.append((abs(gap), paragraph))
        if candidates:
            paragraph = min(candidates, key=lambda c: c[0])[1]
            paragraph['text'] = _join(paragraph['text'], line['text'])
            paragraph['entries'].extend(line['entries'])
            paragraph['box'] = union([paragraph, line])
            paragraph['last_box'] = line['box']
            paragraph['last_text'] = line['text']
        else:
            paragraphs.append(dict(line, last_text=line['text']))
    paragraphs = reading_order(paragraphs, unit)
    return {'text': '\n\n'.join(p['text'] for p in paragraphs), 'paragraphs': [
        {'text': p['text'], 'box': p['box'], 'entry_ids': [e['id'] for e in p['entries']]} for p in paragraphs],
        'ordered_entries': [e for p in paragraphs for e in p['entries']],
        'empty_entries': [e['id'] for e in entries if not e['text'].strip()]}


def recovery_regions(entries, image_size, max_regions=3):
    """Find gaps in repeated short-label alignments, plus uncertain narrow fragments.

    No document names, expected words, dictionaries, or field semantics are used.
    """
    width, page_height = image_size
    visible = [e for e in entries if e['text'].strip()]
    if len(visible) < 8:
        return []
    unit = median(height(e) for e in visible)
    labels = [e for e in visible if 2 <= len(e['text']) <= 10 and
              re.search('[\u4e00-\u9fff]', e['text']) and 2 < (e['box'][2] - e['box'][0]) / height(e) < 7
              and .7 * unit <= height(e) <= 1.8 * unit and e['score'] > .95]
    tracks = []
    for entry in sorted(labels, key=lambda e: e['box'][0]):
        matches = [t for t in tracks if abs(t[0]['box'][0] - entry['box'][0]) < .7 * unit
                   and abs(t[0]['box'][2] - entry['box'][2]) < 1.3 * unit]
        if matches:
            matches[0].append(entry)
        else:
            tracks.append([entry])
    candidates = []
    for track in tracks:
        track = sorted(track, key=center)
        if len(track) < 2:
            continue
        spacings = [center(b) - center(a) for a, b in zip(track, track[1:]) if .8 * unit < center(b) - center(a) < 3 * unit]
        if not spacings:
            continue
        spacing = median(spacings)
        left = median(e['box'][0] for e in track)
        right = median(e['box'][2] for e in track)
        for text in visible:
            if not right < text['box'][0] < right + 2 * unit or len(text['text']) < 3:
                continue
            cy = center(text)
            if any(other['id'] != text['id'] and abs(other['box'][0] - text['box'][0]) < .6 * unit
                   and .3 * unit < cy - center(other) < 1.3 * unit for other in visible):
                continue  # An aligned continuation of a paragraph does not imply a missing label.
            if not center(track[0]) - .2 * spacing < cy < center(track[-1]) + 1.3 * spacing:
                continue
            if any(abs(center(e) - cy) < .65 * spacing for e in track):
                continue
            region = [left - .3 * unit, cy - .8 * unit, right + .3 * unit, cy + .8 * unit]
            if not any(e['text'].strip() and left < (e['box'][0] + e['box'][2]) / 2 < right
                       and abs(center(e) - cy) < .55 * unit for e in visible):
                candidates.append((0, region, 'aligned_gap'))
    for entry in entries:
        b = entry['box']
        if (not entry['text'].strip() or entry['score'] < .8) and height(entry) < 2 * unit:
            candidates.append((1, [b[0] - unit, b[1] - .3 * unit, b[2] + unit, b[3] + .3 * unit], 'uncertain_region'))
    regions = []
    for _, region, reason in sorted(candidates, key=lambda x: x[0]):
        x1, y1, x2, y2 = map(int, region)
        box = [max(0, x1), max(0, y1), min(width, x2), min(page_height, y2)]
        if box[2] - box[0] < 12 or box[3] - box[1] < 12:
            continue
        if any(abs((r['box'][1]+r['box'][3]-box[1]-box[3])/2) < unit
               and abs(r['box'][0]-box[0]) < unit for r in regions):
            continue
        regions.append({'box': box, 'reason': reason})
        if len(regions) == max_regions:
            break
    return regions


def accept_recovery(existing, candidates, region):
    accepted = []
    for candidate in candidates:
        if not candidate['text'].strip() or candidate['score'] < .95:
            continue
        box = candidate['box']
        area = max(1, (box[2]-box[0])*(box[3]-box[1]))
        conflict = False
        for entry in existing + accepted:
            if not entry['text'].strip():
                continue
            b = entry['box']
            overlap = max(0, min(b[2],box[2])-max(b[0],box[0])) * max(0, min(b[3],box[3])-max(b[1],box[1]))
            original_area = max(1, (b[2]-b[0])*(b[3]-b[1]))
            if overlap / min(area, original_area) > .25:
                conflict = True
                break
        if not conflict:
            accepted.append(candidate)
    return accepted
