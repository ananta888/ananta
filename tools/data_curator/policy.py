import json
from typing import Any, Dict, List

# Try to import PyYAML if present, but do not require it
try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


def _fallback_yaml(text: str) -> Dict[str, Any]:
    """Minimal YAML reader that supports only top-level keys and a simple list for 'block_patterns'."""
    data: Dict[str, Any] = {}
    current_list_key: str = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.endswith(':') and not line.startswith('-'):
            key = line[:-1].strip()
            data[key] = []
            current_list_key = key
            continue
        if line.startswith('- '):
            if current_list_key:
                data[current_list_key].append(line[2:].strip())
            else:
                # ignore stray list items
                pass
            continue
        # simple key: value pairs
        if ':' in line:
            key, val = line.split(':', 1)
            data[key.strip()] = val.strip()
    return data


def load_policy(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        text = f.read()
    # JSON first
    try:
        return json.loads(text)
    except Exception:
        pass
    # YAML via PyYAML if available
    if yaml is not None:
        try:
            loaded = yaml.safe_load(text)  # type: ignore
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            pass
    # Fallback minimal
    return _fallback_yaml(text)
