import os
import json
import hashlib
from typing import Any, Dict, Iterable, List
from datetime import datetime, timezone


def ensure_dir(path: str) -> None:
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def read_text(path: str) -> str:
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


def write_text(path: str, text: str) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def write_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    ensure_dir(os.path.dirname(path))
    with open(path, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def list_files_recursive(root: str) -> List[str]:
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden directories like .git
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for fn in filenames:
            if fn.startswith('.'):
                continue
            full = os.path.join(dirpath, fn)
            if os.path.isfile(full):
                files.append(full)
    return files


class _Logger:
    def __init__(self, path: str) -> None:
        self._path = path
        ensure_dir(os.path.dirname(path))
        # open in append mode per write to avoid Windows locks
    def write(self, msg: str) -> None:
        with open(self._path, 'a', encoding='utf-8') as f:
            f.write(msg)


def setup_logger() -> _Logger:
    ts = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    path = os.path.abspath(os.path.join('.','data_curator.logs', f'run-{ts}.log'))
    return _Logger(path)


def stable_random(seed: int):
    # Lightweight wrapper to avoid importing random at top level if not needed
    import random
    rnd = random.Random(seed)
    return rnd
