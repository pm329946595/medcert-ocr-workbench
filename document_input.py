"""Rasterize uploaded documents locally; one canonical PNG per selected page."""
import math,re
from pathlib import Path
from PIL import Image,ImageOps
import pypdfium2 as pdfium

IMAGE_FORMATS={'JPEG','PNG','BMP','TIFF','WEBP','GIF'}
SUFFIXES={'.jpg','.jpeg','.png','.bmp','.tif','.tiff','.webp','.gif','.pdf'}
MAX_PIXELS=25_000_000
MAX_PAGES=50

def select_pages(value,count):
    if not value.strip():
        if count>MAX_PAGES: raise ValueError('文件超过 50 页，请指定本次页码范围。')
        return list(range(count))
    selected=set()
    for piece in value.replace('，',',').split(','):
        match=re.fullmatch(r'\s*(\d+)(?:\s*-\s*(\d+))?\s*',piece)
        if not match: raise ValueError('页码格式应为 1,3-5，页码从 1 开始。')
        a=int(match[1]);b=int(match[2] or a)
        if not 1<=a<=b<=count: raise ValueError(f'页码超出范围，文件共 {count} 页。')
        if b-a+1>MAX_PAGES: raise ValueError('单次最多识别 50 页。')
        selected.update(range(a-1,b))
    if len(selected)>MAX_PAGES: raise ValueError('单次最多识别 50 页。')
    return sorted(selected)

class Document:
    def __init__(self,path,password='',pages=''):
        self.path=Path(path);self.source=None;self.pdf=self.path.suffix.lower()=='.pdf'
        if self.path.suffix.lower() not in SUFFIXES: raise ValueError('文件格式不支持。')
        try:
            if self.pdf:
                self.source=pdfium.PdfDocument(str(self.path),password=password or None)
                self.count=len(self.source)
            else:
                self.source=Image.open(self.path)
                if self.source.format not in IMAGE_FORMATS: raise ValueError('图片实际格式不支持。')
                self.count=getattr(self.source,'n_frames',1) if self.source.format=='TIFF' else 1
            if not self.count: raise ValueError('文件没有可识别页面。')
            self.pages=select_pages(pages,self.count)
        except Exception as exc:
            self.close()
            if isinstance(exc,ValueError): raise
            raise ValueError('文件无法打开；请检查是否损坏、PDF 密码是否正确。') from exc
    def close(self):
        if self.source is not None: self.source.close();self.source=None
    def render(self,index,dpi,destination):
        if self.pdf:
            page=self.source[index]
            try:
                w,h=page.get_size()
                scale=min(dpi/72, math.sqrt(MAX_PIXELS/max(1,w*h)))
                if not math.isfinite(scale) or scale<=0: raise ValueError('PDF 页面尺寸不正确。')
                bitmap=page.render(scale=scale)
                try: image=bitmap.to_pil().convert('RGB')
                finally: bitmap.close()
            finally:page.close()
        else:
            self.source.seek(index)
            w,h=self.source.size
            if w*h>MAX_PIXELS: raise ValueError('图片超过 2500 万像素，请先缩小图片。')
            rgba=ImageOps.exif_transpose(self.source).convert('RGBA')
            image=Image.new('RGB',rgba.size,'white');image.paste(rgba,mask=rgba.getchannel('A'))
        image.save(destination,'PNG')
        size=image.size;image.close()
        return size
