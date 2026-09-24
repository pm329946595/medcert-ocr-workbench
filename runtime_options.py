"""Validated per-request controls; image quality and CPU allocation are independent."""
PRESETS = {
    'fast': dict(det_limit=960, pdf_dpi=120, recovery=False, local_review=False, seal_enhance=False, seal_text=False),
    'balanced': dict(det_limit=1536, pdf_dpi=180, recovery=True, local_review=True, seal_enhance=True, seal_text=False),
    'precise': dict(det_limit=2048, pdf_dpi=240, recovery=True, local_review=True, seal_enhance=True, seal_text=True),
}
def normalize_options(value=None):
    if value is None: value = {}
    if not isinstance(value, dict): raise ValueError('运行参数必须是对象。')
    preset=value.get('preset','balanced')
    if preset not in PRESETS: raise ValueError('请选择快速、均衡或精细模式。')
    unknown=set(value)-set(PRESETS[preset])-{'preset','cpu_threads'}
    if unknown: raise ValueError('包含不支持的运行参数。')
    out=dict(PRESETS[preset],preset=preset)
    for key in ('det_limit','pdf_dpi'):
        n=value.get(key,out[key])
        if isinstance(n,bool) or not isinstance(n,int): raise ValueError('清晰度参数必须是整数。')
        low,high=(768,2560) if key=='det_limit' else (96,300)
        if not low<=n<=high: raise ValueError(f'{key} 超出允许范围 {low}–{high}。')
        out[key]=n
    for key in ('recovery','local_review','seal_enhance','seal_text'):
        if key in value and not isinstance(value[key],bool): raise ValueError('开关参数必须为布尔值。')
        out[key]=value.get(key,out[key])
    from cpu_resources import normalize_threads
    out['cpu_threads']=normalize_threads(value.get('cpu_threads'))
    return out
