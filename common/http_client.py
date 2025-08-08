import json
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def http_get(url: str, retries: int = 5, delay: float = 1.0, timeout: float = 10.0) -> Any:
    """Perform a GET request with retry and basic JSON decoding.

    Parameters
    ----------
    url: str
        Target URL.
    retries: int
        Number of attempts before giving up.
    delay: float
        Delay in seconds between retries.
    timeout: float
        Timeout in seconds passed to ``urllib.request.urlopen``.
    """
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
                raise last_err


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
    """Perform a POST request with retry and basic JSON decoding."""
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
                raise last_err
