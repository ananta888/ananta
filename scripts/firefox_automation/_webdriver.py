#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from typing import Optional
from urllib import request

from firefox_automation._config import BASE


def wd(method: str, path: str, payload=None, timeout: int = 45):
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = request.Request(BASE + path, data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def js(session_id: str, script: str, args=None):
    return wd("POST", f"/session/{session_id}/execute/sync", {"script": script, "args": args or []})


def js_async(session_id: str, script: str, args=None, timeout: int = 45):
    return wd("POST", f"/session/{session_id}/execute/async", {"script": script, "args": args or []}, timeout=timeout)


def _extract_element_id(raw: dict) -> Optional[str]:
    value = raw.get("value") if isinstance(raw, dict) else None
    if isinstance(value, dict):
        return str(value.get("element-6066-11e4-a52e-4f735466cecf") or value.get("ELEMENT") or "")
    return None


def find_element(session_id: str, css_selector: str, timeout: int = 20) -> Optional[str]:
    end = time.time() + timeout
    while time.time() < end:
        try:
            raw = wd(
                "POST",
                f"/session/{session_id}/element",
                {"using": "css selector", "value": css_selector},
            )
            element_id = _extract_element_id(raw)
            if element_id:
                return element_id
        except Exception:
            pass
        time.sleep(0.4)
    return None


def element_clear(session_id: str, element_id: str):
    wd("POST", f"/session/{session_id}/element/{element_id}/clear", {})


def element_send_keys(session_id: str, element_id: str, text: str):
    wd(
        "POST",
        f"/session/{session_id}/element/{element_id}/value",
        {"text": text, "value": list(text)},
    )


def element_click(session_id: str, element_id: str):
    wd("POST", f"/session/{session_id}/element/{element_id}/click", {})


def set_input_value_via_js(session_id: str, selector: str, text: str) -> bool:
    result = js(
        session_id,
        """
        const selector = arguments[0];
        const text = arguments[1];
        const input = document.querySelector(selector);
        if (!input) return false;
        input.focus();
        input.value = text;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
        """,
        [selector, text],
    )
    return bool(result.get("value"))


def wait_for(session_id: str, script: str, timeout: int = 25) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            out = js(session_id, script)
            if out.get("value"):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False
