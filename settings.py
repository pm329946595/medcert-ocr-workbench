"""Project-scoped caches and CPU-only settings, loaded before third-party imports."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
from cpu_hardware import detect_profile

CPU_PROFILE = detect_profile()
CPU_THREADS = CPU_PROFILE['default_threads']
for folder in ('models', 'cache', 'cache/tmp', 'cache/gradio', 'cache/huggingface',
               'cache/modelscope', 'cache/paddle', 'images', 'outputs', 'logs', 'work'):
    (ROOT / folder).mkdir(parents=True, exist_ok=True)
for key, value in {
    'PADDLE_PDX_CACHE_HOME': ROOT / 'models',
    'HF_HOME': ROOT / 'cache/huggingface',
    'MODELSCOPE_CACHE': ROOT / 'cache/modelscope',
    'PADDLE_HOME': ROOT / 'cache/paddle',
    'GRADIO_TEMP_DIR': ROOT / 'cache/gradio',
    'TEMP': ROOT / 'cache/tmp', 'TMP': ROOT / 'cache/tmp',
    'CUDA_VISIBLE_DEVICES': '',
    'GRADIO_ANALYTICS_ENABLED': 'False',
    'HF_HUB_DISABLE_TELEMETRY': '1',
    'PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK': 'True',
    'PADDLE_PDX_MODEL_SOURCE': 'bos',
    'OMP_NUM_THREADS': str(CPU_THREADS), 'MKL_NUM_THREADS': str(CPU_THREADS),
    'OPENBLAS_NUM_THREADS': str(CPU_THREADS), 'OMP_WAIT_POLICY': 'PASSIVE',
    'KMP_BLOCKTIME': '0', 'PYTHONUTF8': '1',
}.items():
    os.environ[key] = str(value)
