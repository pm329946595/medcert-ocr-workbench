"""Small local visual-risk observations, independent of credential answers.

Color is not a stamp classifier. The red flag means substantial red ink actually
overlaps a field, after removing isolated/subpixel colored edges of black text.
Watermark flags use detected OCR polygons; an undetected watermark remains a
known limitation, not proof that the image is free of interference.
"""
from math import atan2, degrees
from pathlib import Path
import re
from statistics import median
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw


WATERMARK_WORDS = re.compile(
    r'仅供|仅限.{0,16}(?:使用|展示)|他用无效|网站展示|公司网站|不得.{0,8}他用|供.{0,8}展示|仅用于.{0,12}展示',
    re.I,
)


def _geometry(entry):
    polygon = entry.get('polygon')
    if not polygon or len(polygon) < 3:
        return None
    points = np.asarray(polygon, dtype=np.float32).reshape(-1, 2)
    if not np.isfinite(points).all() or len(points) < 3:
        return None
    hull = cv2.convexHull(points).reshape(-1, 2)
    if cv2.contourArea(hull) <= 0:
        return None
    delta = points[1] - points[0]
    angle = degrees(atan2(float(delta[1]), float(delta[0])))
    while angle > 90:
        angle -= 180
    while angle <= -90:
        angle += 180
    sides = [float(np.linalg.norm(points[(i+1) % len(points)]-points[i])) for i in range(len(points))]
    return {'polygon': hull, 'angle': angle, 'height': max(1.0, min(sides)),
            'width': max(sides), 'area': float(cv2.contourArea(hull))}


class RiskAnalyzer:
    def __init__(self, image, entries):
        started = time.perf_counter()
        self.width, self.height = image.size
        geometry = [(entry, _geometry(entry)) for entry in entries]
        geometry = [(entry, geo) for entry, geo in geometry if geo is not None]
        ordinary = [geo for entry, geo in geometry if len(entry.get('text', '').strip()) >= 3 and geo['width'] >= 2*geo['height']]
        self.text_height = median(g['height'] for g in ordinary) if ordinary else max(10, min(image.size)/50)
        # A tilted whole page is not itself a diagonal watermark. Use its smaller
        # text lines to estimate the page's usual baseline direction.
        page_lines = [g for g in ordinary if g['height'] <= 1.6*self.text_height]
        self.page_angle = median(g['angle'] for g in page_lines) if page_lines else 0
        self.watermarks = []
        for entry, geo in geometry:
            text = re.sub(r'\s+', '', entry.get('text', ''))
            explicit = bool(WATERMARK_WORDS.search(text))
            tilt = abs(geo['angle'] - self.page_angle)
            tilt = min(tilt, 180-tilt)
            large_diagonal = (18 < tilt < 78 and geo['height'] >= 1.8*self.text_height
                              and geo['width'] >= 2.2*self.text_height)
            if explicit or large_diagonal:
                self.watermarks.append({'id':entry.get('id'), 'text':entry.get('text', ''),
                    'polygon':geo['polygon'].tolist(), 'angle':round(geo['angle'],2),
                    'reason':'watermark_words' if explicit else 'large_diagonal_text',
                    'area':round(geo['area'],2)})
        rgb = np.asarray(image.convert('RGB')).astype(np.int16)
        red, green, blue = rgb[:,:,0], rgb[:,:,1], rgb[:,:,2]
        chroma = red-np.maximum(green, blue)
        # Pink faded ink is accepted too. Orange/yellow subpixel fringes are
        # rejected by green/blue balance; remaining one-pixel red fringes have
        # no 2x2 thickness core and are discarded by connected component.
        # Faded pink can have blue above green; pale gold security paper has
        # green substantially above blue and must not be classified as red.
        balanced = (green-blue) <= np.maximum(6, chroma*.45)
        candidate = ((red >= 90) & (chroma >= 14) & balanced).astype(np.uint8)
        core = cv2.erode(candidate, np.ones((2,2),np.uint8), anchor=(0,0),
                         borderType=cv2.BORDER_CONSTANT, borderValue=0)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, connectivity=8)
        # Erosion coordinates denote the top-left pixel of a genuine red 2x2
        # square. Preserve original component coordinates, without dilation drift.
        core_counts = np.bincount(labels[core > 0], minlength=count)
        keep = np.zeros(count, dtype=np.uint8)
        self.red_components = []
        for index in range(1, count):
            x,y,width,height,area = map(int,stats[index])
            if (area < max(6,.06*self.text_height**2) or core_counts[index] < 1
                    or min(width,height) < max(3,.12*self.text_height)
                    or max(width,height) < max(5,.35*self.text_height)):
                continue
            # A near-uniform page tint is not a stamp. High saturation red paper
            # is outside this heuristic, and is reported as a limitation.
            if area > self.width*self.height*.12 and area/max(1,width*height) > .65:
                continue
            keep[index] = 1
            self.red_components.append({'box':[x,y,x+width,y+height], 'area':area,
                                        'core_pixels':int(core_counts[index])})
        self.red_mask = keep[labels]
        self.red_pixels = int(self.red_mask.sum())
        self.red_components.sort(key=lambda component:-component['area'])
        self.seconds = round(time.perf_counter()-started,4)

    def for_box(self, box):
        if not isinstance(box,(list,tuple,np.ndarray)) or len(box)!=4:
            return self._empty('invalid_box')
        values = np.asarray(box,dtype=float)
        if not np.isfinite(values).all():
            return self._empty('invalid_box')
        x1,y1,x2,y2 = values
        left,top = max(0,int(np.floor(x1))),max(0,int(np.floor(y1)))
        right,bottom = min(self.width,int(np.ceil(x2))),min(self.height,int(np.ceil(y2)))
        if right<=left or bottom<=top:
            return self._empty('empty_box')
        pixels = (right-left)*(bottom-top)
        red_pixels = int(self.red_mask[top:bottom,left:right].sum())
        red_ratio = red_pixels/pixels
        red_overlap = red_pixels>=4 and red_ratio>=.003
        query = np.asarray([[left,top],[right,top],[right,bottom],[left,bottom]],dtype=np.float32)
        hits = []
        for watermark in self.watermarks:
            area, _ = cv2.intersectConvexConvex(query,np.asarray(watermark['polygon'],dtype=np.float32))
            overlap = max(0,float(area))
            if overlap >= max(8,.03*self.text_height**2) and overlap/pixels>=.015:
                hits.append({'id':watermark['id'], 'text':watermark['text'], 'reason':watermark['reason'],
                             'polygon':watermark['polygon'], 'intersection_area':round(overlap,2),
                             'field_overlap_ratio':round(overlap/pixels,4)})
        return {'red_ink_ratio':round(red_ratio,6), 'red_stamp_overlap':bool(red_overlap),
                'watermark_overlap':bool(hits), 'details':{'red_pixels':red_pixels,'query_pixels':pixels,
                    'watermarks':hits,'method':'balanced_red_thickness_components_and_ocr_polygons'}}

    @staticmethod
    def _empty(reason):
        return {'red_ink_ratio':0.0,'red_stamp_overlap':False,'watermark_overlap':False,
                'details':{'reason':reason,'watermarks':[],'red_pixels':0,'query_pixels':0}}

    def as_dict(self):
        return {'method':'balanced_red_thickness_components_and_ocr_polygons',
                'seconds':self.seconds,'image_size':[self.width,self.height],
                'text_height':round(self.text_height,3),'page_angle':round(self.page_angle,3),
                'red_pixels':self.red_pixels,'red_component_count':len(self.red_components),
                'red_components':self.red_components,'watermarks':self.watermarks,
                'limits':['红色风险指有厚度的红色印迹重叠，不证明一定是印章。',
                          '水印依赖已有 OCR 多边形；未检测出的灰色水印可能遗漏。',
                          '黑白印章、极淡或只有单像素宽的红线可能遗漏；风险阴性不等于没有遮挡。']}

    def save_debug(self, image, destination):
        """Save local QA overlays without changing the original image."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True,exist_ok=True)
        original = image.convert('RGB')
        overlay = original.copy()
        draw = ImageDraw.Draw(overlay)
        for watermark in self.watermarks:
            polygon = [tuple(point) for point in watermark['polygon']]
            draw.polygon(polygon,fill='#bce2ee')
        overlay = Image.blend(original,overlay,.45)
        array = np.asarray(overlay).copy()
        selected = self.red_mask.astype(bool)
        array[selected] = (array[selected].astype(np.uint16)+np.array([255,0,180]))//2
        result = Image.fromarray(array)
        draw = ImageDraw.Draw(result)
        for watermark in self.watermarks:
            polygon = [tuple(point) for point in watermark['polygon']]
            draw.line(polygon+[polygon[0]],fill='#006ca4',width=2)
        result.save(destination)
        Image.fromarray(self.red_mask*255).save(destination.with_name(destination.stem+'_red_mask.png'))


def ink_risk(image, entries):
    return RiskAnalyzer(image,entries)
