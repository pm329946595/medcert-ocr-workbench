"""CPU resource controls independent of image quality and parser rules."""
import time,threading
import psutil
from settings import CPU_PROFILE
LOGICAL=CPU_PROFILE['logical_processors']
PHYSICAL=CPU_PROFILE['physical_cores']
AVAILABLE=CPU_PROFILE['available_processors']
MAX_THREADS=CPU_PROFILE['max_threads']
DEFAULT_THREADS=CPU_PROFILE['default_threads']
RECOMMENDED_THREADS=CPU_PROFILE['recommended_threads']
CHOICES=CPU_PROFILE['choices']
_LOCK=threading.Lock();_sample=None;_last=None

def normalize_threads(value=None):
    value=DEFAULT_THREADS if value is None else value
    if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=MAX_THREADS:
        raise ValueError(f'CPU 并行度必须是 1 到 {MAX_THREADS} 之间的整数。')
    return value

def capabilities():
    return {**CPU_PROFILE,'choices':list(CHOICES)}

def snapshot():
    global _sample,_last
    with _LOCK:
        now=time.monotonic()
        if _sample is not None and now-_sample['sampled_at']<1:return dict(_sample)
        proc=psutil.Process();cpu=sum(proc.cpu_times()[:2])
        percent=None if _last is None else max(0,min(100,100*(cpu-_last[1])/(now-_last[0])/LOGICAL))
        _last=(now,cpu)
        from ocr_service import engine_threads
        _sample={'sampled_at':now,'cpu_machine_percent':round(percent,1) if percent is not None else None,
                 'memory_mib':round(proc.memory_info().rss/2**20,1),'engine_threads':engine_threads(),
                 'logical_processors':LOGICAL,'physical_cores':PHYSICAL,'available_processors':AVAILABLE}
        return dict(_sample)
