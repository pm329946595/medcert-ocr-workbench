from pathlib import Path
import json
import psutil

root = Path(__file__).resolve().parent
state = root / 'work/server.json'
if state.exists():
    info = json.loads(state.read_text(encoding='utf-8'))
    try:
        process = psutil.Process(info['pid'])
        if abs(process.create_time() - info['create_time']) < 0.01 and any(
            Path(arg).resolve() == root / 'app.py' for arg in process.cmdline()[1:] if not arg.startswith('-')
        ):
            children = process.children(recursive=True)
            for child in children:
                child.terminate()
            process.terminate()
            psutil.wait_procs([process] + children, timeout=10)
            print('OCR stopped. Memory released.')
        else:
            print('Stale state: no matching OCR process was stopped.')
    except psutil.NoSuchProcess:
        print('OCR is already stopped.')
    state.unlink(missing_ok=True)
else:
    print('OCR is not running.')
