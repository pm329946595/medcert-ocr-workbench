"""Optional local API configuration."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from settings import ROOT

def load_configuration(path=None):
    path = Path(path) if path else ROOT / 'config.json'
    if not path.exists():
        return {'allowed_origins': []}
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or set(data) != {'allowed_origins'}:
        raise ValueError('config.json 仅支持 allowed_origins 数组。')
    origins = data['allowed_origins']
    if not isinstance(origins, list):
        raise ValueError('allowed_origins 必须为数组。')
    for origin in origins:
        if not isinstance(origin, str):
            raise ValueError('访问来源必须为字符串。')
        parsed = urlsplit(origin)
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError('访问来源端口不正确。') from exc
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.path or parsed.query or parsed.fragment or
                parsed.username or parsed.password or '*' in origin):
            raise ValueError('访问来源仅填写协议、主机和端口。')
    return {'allowed_origins': list(dict.fromkeys(origins))}

CONFIG = load_configuration()
