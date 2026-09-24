"""Give Windows native model readers an ASCII directory alias when needed."""
import hashlib,json,os
from pathlib import Path
from settings import ROOT

def model_directory(directory):
    directory=Path(directory).resolve()
    if os.name!='nt' or str(directory).isascii():return str(directory)
    import ctypes,_winapi
    short=ctypes.create_unicode_buffer(32768)
    if ctypes.windll.kernel32.GetShortPathNameW(str(directory),short,len(short)) and short.value.isascii():return short.value
    candidates=[]
    if os.environ.get('LOCALAPPDATA'):candidates.append(Path(os.environ['LOCALAPPDATA'])/'Temp')
    if os.environ.get('PUBLIC'):candidates.append(Path(os.environ['PUBLIC']))
    if os.environ.get('SystemRoot'):candidates.append(Path(os.environ['SystemRoot'])/'Temp')
    name=hashlib.sha256(str(directory).encode('utf-8')).hexdigest()[:20]
    for parent in candidates:
        if not str(parent).isascii():continue
        alias=parent/'MedCert-OCR-ModelAccess'/name
        try:
            alias.parent.mkdir(parents=True,exist_ok=True)
            if not alias.exists():
                try:_winapi.CreateJunction(str(directory),str(alias))
                except FileExistsError:pass
            if alias.resolve()!=directory:continue
            (ROOT/'work/model-access.json').write_text(json.dumps({'location':str(alias.parent),'purpose':'Windows model path compatibility'},ensure_ascii=False),encoding='utf-8')
            return str(alias)
        except OSError:continue
    raise RuntimeError('当前 Windows 无法建立模型目录访问路径。请将完整程序移到有写权限的英文目录后重新启动。')
