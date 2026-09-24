"""Conservative ruled-table geometry; no OCR, document templates or field answers.

Coordinates returned by this module are always in the original image space.
The grid is optional evidence: an incomplete set of borders returns None rather
than assigning text across uncertain cells. Supports labels vertically centered
against a multiline value by locating both horizontal cell borders.
"""
from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class TableGeometry:
    width: int
    height: int
    horizontal: list = field(default_factory=list)
    vertical: list = field(default_factory=list)
    text_height: float = 12.0
    scale: float = 1.0

    def right_cell(self, label_box):
        """Find a fully bordered adjacent cell to the right of a label box.

        A result requires three vertical tracks, two horizontal borders crossing
        both cells, and the complete label to fit its own cell. A certificate
        border alone therefore does not create a table. Empty right cells are
        allowed; their presence is useful for distinguishing missing content.
        Confidence is geometric support, not a calibrated probability of OCR truth.
        """
        if len(label_box) != 4:
            return None
        x1, y1, x2, y2 = map(float, label_box)
        if x2 <= x1 or y2 <= y1:
            return None
        cy=(y1+y2)/2
        tolerance=max(2.0, self.text_height*.32)
        tracks=[v for v in self.vertical if v['start']-tolerance <= cy <= v['end']+tolerance]
        candidates=[]
        for left, divider, right in zip(tracks,tracks[1:],tracks[2:]):
            lx,dx,rx=left['position'],divider['position'],right['position']
            if not (lx-tolerance <= x1 and x2 <= dx+tolerance):
                continue
            if x1 >= dx-tolerance or dx-lx < self.text_height or rx-dx < self.text_height*1.5:
                continue
            crossing=[h for h in self.horizontal if h['start'] <= lx+tolerance*2
                      and h['end'] >= rx-tolerance*2]
            upper=[h for h in crossing if h['position'] <= cy]
            lower=[h for h in crossing if h['position'] > cy]
            if not upper or not lower:
                continue
            top=max(upper,key=lambda h:h['position'])
            bottom=min(lower,key=lambda h:h['position'])
            ty,by=top['position'],bottom['position']
            if y1 < ty-tolerance or y2 > by+tolerance:
                continue
            if by-ty < (y2-y1)*.7 or by-ty > self.height*.5:
                continue
            # Check that the three vertical lines really connect these borders.
            overlaps=[max(0,min(v['end'],by)-max(v['start'],ty))/(by-ty) for v in (left,divider,right)]
            if min(overlaps) < .86:
                continue
            coverage=min((top['end']-top['start'])/(rx-lx),(bottom['end']-bottom['start'])/(rx-lx),1.0)
            confidence=min(.99, .70+.15*min(overlaps)+.14*coverage)
            inset=max(.75, min(2.5,self.text_height*.06))
            candidates.append({'box':[round(dx+inset,2),round(ty+inset,2),round(rx-inset,2),round(by-inset,2)],
                'confidence':round(confidence,3),'method':'ruled_table_adjacent_cell',
                'label_cell':[round(lx,2),round(ty,2),round(dx,2),round(by,2)],
                'border_evidence':{'horizontal_y':[round(ty,2),round(by,2)],
                                   'vertical_x':[round(lx,2),round(dx,2),round(rx,2)]}})
        if len(candidates)!=1:
            return None
        return candidates[0]

    def as_dict(self):
        return {'method':'relative_morphology_ruled_grid','image_size':[self.width,self.height],
                'horizontal_lines':self.horizontal,'vertical_lines':self.vertical,
                'text_height':round(self.text_height,2),'geometry_scale':round(self.scale,5),
                'candidate_grid':len(self.horizontal)>=2 and len(self.vertical)>=3}


def _segments(mask,horizontal,scale,min_length):
    """Long line components, merging parallel stroke edges but not adjacent rows."""
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask,8)
    found=[]
    for x,y,w,h,area in stats[1:]:
        length=w if horizontal else h
        thickness=h if horizontal else w
        if length<min_length or length/max(1,thickness)<12:
            continue
        # A component's enclosing box gives a stable center even for thin ink.
        position=(y+(h-1)/2) if horizontal else (x+(w-1)/2)
        start=x if horizontal else y
        end=x+w-1 if horizontal else y+h-1
        found.append({'position':float(position)/scale,'start':float(start)/scale,
                      'end':float(end)/scale,'thickness':float(thickness)/scale})
    found.sort(key=lambda r:r['position'])
    merged=[]
    for line in found:
        previous=merged[-1] if merged else None
        if previous and abs(previous['position']-line['position'])<=3/scale:
            previous['position']=(previous['position']+line['position'])/2
            previous['start']=min(previous['start'],line['start'])
            previous['end']=max(previous['end'],line['end'])
            previous['thickness']=max(previous['thickness'],line['thickness'])
        else:
            merged.append(line)
    return [{k:round(v,2) for k,v in line.items()} for line in merged]


def detect_grid(image,entries):
    """Find image-supported ruling lines using only relative geometry.

    This CPU-only pass is much cheaper than another OCR pass. At most 1800px is
    used for line finding only; the OCR image and recognition crops are unchanged.
    Entries contribute a robust text height, never a document type or field value.
    """
    width,height=image.size
    heights=[float(e['box'][3]-e['box'][1]) for e in entries
             if e.get('box') and e['box'][3]>e['box'][1]]
    unit=float(np.median(heights)) if heights else max(8,min(width,height)/60)
    scale=min(1.0,1800/max(width,height))
    rgb=np.asarray(image.convert('RGB'))
    if scale<1:
        rgb=cv2.resize(rgb,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    h,w=gray.shape
    block=max(15,int(min(w,h)/28)|1)
    ink=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                             cv2.THRESH_BINARY_INV,block,13)
    # Mild perpendicular dilation tolerates thin/antialiased scanned lines.
    horizontal=cv2.morphologyEx(cv2.dilate(ink,np.ones((2,1),np.uint8)),cv2.MORPH_OPEN,
                               np.ones((1,max(25,w//9)),np.uint8))
    # Scanned thin divider strokes can have short gaps. Close only gaps smaller
    # than one text height; do not infer a divider across a missing field region.
    gap=max(3,int(unit*scale*.65)|1)
    vertical_ink=cv2.morphologyEx(ink,cv2.MORPH_CLOSE,np.ones((gap,1),np.uint8))
    vertical=cv2.morphologyEx(cv2.dilate(vertical_ink,np.ones((1,2),np.uint8)),cv2.MORPH_OPEN,
                             np.ones((max(25,h//14),1),np.uint8))
    hs=_segments(horizontal,True,scale,w*.22)
    vs=_segments(vertical,False,scale,h*.20)
    supported=[]
    for v in vs:
        px=int(round(v['position']*scale))
        pa=max(0,int(v['start']*scale)); pb=min(h,int(v['end']*scale)+1)
        radius=max(1,int(np.ceil(v['thickness']*scale/2)))
        strip=ink[pa:pb,max(0,px-radius):min(w,px+radius+1)]
        ink_support=float((strip>0).mean(axis=0).max()) if strip.size else 0
        # Closing can connect aligned printed letters, but a genuine near-vertical
        # ruling line retains substantially more continuous original ink.
        if ink_support>=.55:
            v['ink_support']=round(ink_support,3)
            supported.append(v)
    vs=supported
    # Require repeated physical intersections, rejecting vertically aligned text
    # strokes that a gap-closing operation might otherwise promote to a divider.
    tol=max(2,unit*.2)
    vs=[v for v in vs if sum(h['start']-tol<=v['position']<=h['end']+tol
           and v['start']-tol<=h['position']<=v['end']+tol for h in hs)>=3]
    return TableGeometry(width,height,hs,vs,unit,scale)
