import collections as _collections
import logging
import time
from collections import defaultdict
from typing import Any

CIRCUIT_BREAKER = {"failures": defaultdict(int), "last_failure": defaultdict(float), "open": defaultdict(bool)}
_CB_DEFAULT_THRESHOLD = 5
_CB_DEFAULT_RECOVERY_TIME = 60

_RATE_LIMIT_WINDOW: dict[str, _collections.deque] = defaultdict(lambda: _collections.deque())
_RATE_LIMIT_LOCK = __import__("threading").Lock()

_ERR_RATE_LOCK = __import__("threading").Lock()
_ERR_SUCCESS_WINDOW: dict[str, _collections.deque] = defaultdict(lambda: _collections.deque())
_ERR_FAILURE_WINDOW: dict[str, _collections.deque] = defaultdict(lambda: _collections.deque())


def _cb_config() -> tuple[int, int]:
    try:
        from flask import current_app
        cfg = (current_app.config.get("AGENT_CONFIG") or {}).get("llm_config") or {}
        threshold = int(cfg.get("circuit_breaker_threshold") or _CB_DEFAULT_THRESHOLD)
        recovery = int(cfg.get("circuit_breaker_open_seconds") or _CB_DEFAULT_RECOVERY_TIME)
        return max(1, threshold), max(5, recovery)
    except RuntimeError:
        return _CB_DEFAULT_THRESHOLD, _CB_DEFAULT_RECOVERY_TIME


def _check_circuit_breaker(provider: str) -> bool:
    _, recovery_time = _cb_config()
    if CIRCUIT_BREAKER["open"][provider]:
        if time.time() - CIRCUIT_BREAKER["last_failure"][provider] > recovery_time:
            logging.info("circuit_breaker provider=%s state=half_open", provider)
            CIRCUIT_BREAKER["open"][provider] = False
            CIRCUIT_BREAKER["failures"][provider] = 0
            return True
        return False
    return True


def _report_llm_failure(provider: str) -> None:
    threshold, _ = _cb_config()
    CIRCUIT_BREAKER["failures"][provider] += 1
    CIRCUIT_BREAKER["last_failure"][provider] = time.time()
    if CIRCUIT_BREAKER["failures"][provider] >= threshold:
        if not CIRCUIT_BREAKER["open"][provider]:
            logging.error(
                "circuit_breaker_open provider=%s failures=%s",
                provider,
                CIRCUIT_BREAKER["failures"][provider],
            )
            CIRCUIT_BREAKER["open"][provider] = True
    _record_llm_failure_rate(provider)


def _report_llm_success(provider: str) -> None:
    CIRCUIT_BREAKER["failures"][provider] = 0
    CIRCUIT_BREAKER["open"][provider] = False
    now = time.time()
    with _ERR_RATE_LOCK:
        _ERR_SUCCESS_WINDOW[provider].append(now)


def _record_llm_failure_rate(provider: str) -> None:
    now = time.time()
    with _ERR_RATE_LOCK:
        _ERR_FAILURE_WINDOW[provider].append(now)


def get_provider_error_rate(provider: str, window_s: float = 60.0) -> dict:
    now = time.time()
    cutoff = now - window_s
    with _ERR_RATE_LOCK:
        s_dq = _ERR_SUCCESS_WINDOW[provider]
        f_dq = _ERR_FAILURE_WINDOW[provider]
        while s_dq and s_dq[0] < cutoff:
            s_dq.popleft()
        while f_dq and f_dq[0] < cutoff:
            f_dq.popleft()
        successes = len(s_dq)
        failures = len(f_dq)
    total = successes + failures
    error_rate = round(failures / total, 3) if total > 0 else 0.0
    return {
        "provider": provider,
        "window_seconds": window_s,
        "successes": successes,
        "failures": failures,
        "total": total,
        "error_rate": error_rate,
    }


def _rl_config(provider: str) -> int:
    try:
        from flask import current_app
        cfg = (current_app.config.get("AGENT_CONFIG") or {}).get("llm_config") or {}
        rl = cfg.get("rate_limit_rpm") or 0
        rl_per = cfg.get(f"rate_limit_rpm_{provider}") or rl
        return max(0, int(rl_per))
    except (RuntimeError, TypeError, ValueError):
        return 0


def _check_rate_limit(provider: str) -> bool:
    rpm = _rl_config(provider)
    if rpm <= 0:
        return True
    now = time.time()
    window_start = now - 60.0
    with _RATE_LIMIT_LOCK:
        dq = _RATE_LIMIT_WINDOW[provider]
        while dq and dq[0] < window_start:
            dq.popleft()
        if len(dq) >= rpm:
            logging.warning(
                "rate_limit_exceeded provider=%s requests_in_window=%s limit_rpm=%s",
                provider, len(dq), rpm,
            )
            return False
        dq.append(now)
        return True


def get_rate_limit_state(provider: str) -> dict:
    rpm = _rl_config(provider)
    now = time.time()
    window_start = now - 60.0
    with _RATE_LIMIT_LOCK:
        dq = _RATE_LIMIT_WINDOW[provider]
        while dq and dq[0] < window_start:
            dq.popleft()
        count = len(dq)
    return {
        "provider": provider,
        "requests_in_last_60s": count,
        "limit_rpm": rpm,
        "enabled": rpm > 0,
    }


def get_circuit_breaker_state(provider: str) -> dict:
    threshold, recovery_time = _cb_config()
    is_open = bool(CIRCUIT_BREAKER["open"][provider])
    last_failure = float(CIRCUIT_BREAKER["last_failure"][provider] or 0)
    failures = int(CIRCUIT_BREAKER["failures"][provider] or 0)
    age_s = round(time.time() - last_failure, 1) if last_failure else None
    return {
        "provider": provider,
        "state": "open" if is_open else "closed",
        "failures": failures,
        "threshold": threshold,
        "recovery_seconds": recovery_time,
        "last_failure_age_seconds": age_s,
    }
