"""Small HTTP helper functions with retry support."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

logger = logging.getLogger(__name__)


def http_get(url: str, retries: int = 5, delay: float = 1.0, timeout: float = 10.0) -> Any:
    """Perform a GET request with retries and JSON decoding."""

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                raw = r.read().decode()
                try:
                    return json.loads(raw)
                except Exception:
                    return raw
        except urllib.error.URLError as e:  # pragma: no cover - network failures
            last_err = e
            if attempt < retries:
                logger.warning(
                    "[http_get] attempt %s/%s failed: %s", attempt, retries, e
                )
                time.sleep(delay)
            else:
                logger.error("[http_get] failed after %s attempts: %s", retries, e)
                return None


def http_post(
    url: str,
    data: Dict[str, Any],
    *,
    form: bool = False,
    headers: Dict[str, str] | None = None,
    retries: int = 5,
    delay: float = 1.0,
    timeout: float = 10.0,
) -> Any:
    """Perform a POST request with retries and JSON decoding."""

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            if form:
                body = urllib.parse.urlencode(data).encode()
                hdrs = headers or {}
            else:
                body = json.dumps(data).encode()
                hdrs = {"Content-Type": "application/json"}
                if headers:
                    hdrs.update(headers)
            req = urllib.request.Request(url, data=body, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = r.read().decode()
                try:
                    return json.loads(resp)
                except Exception:
                    return resp
        except urllib.error.URLError as e:  # pragma: no cover - network failures
            last_err = e
            if attempt < retries:
                logger.warning(
                    "[http_post] attempt %s/%s failed: %s", attempt, retries, e
                )
                time.sleep(delay)
            else:
                logger.error("[http_post] failed after %s attempts: %s", retries, e)
                return None

