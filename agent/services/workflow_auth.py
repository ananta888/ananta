from __future__ import annotations

import hashlib
import hmac
import time


def sign_callback(*, secret: str, correlation_id: str, provider: str, timestamp: int | None = None) -> dict[str, str]:
    ts = int(timestamp or time.time())
    payload = f"{provider}:{correlation_id}:{ts}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"timestamp": str(ts), "signature": sig}


def verify_callback_signature(*, secret: str, correlation_id: str, provider: str, timestamp: str, signature: str, max_age_seconds: int = 300) -> bool:
    try:
        ts = int(timestamp)
    except Exception:
        return False
    now = int(time.time())
    if abs(now - ts) > max_age_seconds:
        return False
    expected = sign_callback(secret=secret, correlation_id=correlation_id, provider=provider, timestamp=ts)["signature"]
    return hmac.compare_digest(expected, str(signature or ""))
