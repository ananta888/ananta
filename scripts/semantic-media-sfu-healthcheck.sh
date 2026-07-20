#!/usr/bin/env bash
set -euo pipefail

base_url="${ANANTA_SEMANTIC_MEDIA_SFU_URL:-http://127.0.0.1:7880}"
timeout_seconds="${ANANTA_SEMANTIC_MEDIA_SFU_HEALTH_TIMEOUT_SECONDS:-5}"

python3 - "$base_url" "$timeout_seconds" <<'PY'
import sys
import urllib.error
import urllib.request

url = sys.argv[1].rstrip("/") + "/"
timeout = float(sys.argv[2])
try:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        status = int(response.status)
except (OSError, ValueError, urllib.error.URLError) as exc:
    raise SystemExit(f"semantic-media SFU unavailable: {exc}") from exc
if status < 200 or status >= 500:
    raise SystemExit(f"semantic-media SFU unhealthy: HTTP {status}")
print(f"semantic-media SFU ready: {url} (HTTP {status})")
PY
