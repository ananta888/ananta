#!/usr/bin/env python3
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict

BASE = os.getenv("ANANTA_SELENIUM_URL", "http://127.0.0.1:4444/wd/hub")
APP_BASE = os.getenv("ANANTA_FRONTEND_URL", "http://angular-frontend:4200")
HUB_BASE = os.getenv("HUB_BASE_URL", "http://127.0.0.1:5000")
HUB_CONTAINER = os.getenv("ANANTA_HUB_CONTAINER", "ananta-ai-agent-hub-1")
DEFAULT_REPORT_DIR = Path("test-reports/live-click")
DEFAULT_PHASES = ["setup", "goal", "execution", "benchmark", "review"]
FILE_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_./-])(?:[A-Za-z0-9_.-]+/){1,}[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,10}")

def _read_repo_dotenv() -> Dict[str, str]:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return {}
    values: Dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


_DOTENV = _read_repo_dotenv()
LOGIN_USER = os.getenv("E2E_ADMIN_USER") or os.getenv("INITIAL_ADMIN_USER") or _DOTENV.get("INITIAL_ADMIN_USER") or "admin"
LOGIN_PASS = (
    os.getenv("E2E_ADMIN_PASSWORD")
    or os.getenv("INITIAL_ADMIN_PASSWORD")
    or _DOTENV.get("INITIAL_ADMIN_PASSWORD")
    or "test123"
)
