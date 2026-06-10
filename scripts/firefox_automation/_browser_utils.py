#!/usr/bin/env python3
from __future__ import annotations

import time
from typing import Dict, List

from firefox_automation._webdriver import js


def current_route_and_title(session_id: str) -> Dict[str, str]:
    out = js(
        session_id,
        """
        const h=document.querySelector('h1,h2,h3');
        return {
          path: location.pathname + location.search + location.hash,
          title: h ? h.textContent.trim() : (document.title || '')
        };
        """,
    ).get("value") or {}
    return {"path": str(out.get("path") or ""), "title": str(out.get("title") or "")}


def list_visible_errors(session_id: str) -> List[Dict[str, str]]:
    out = js(
        session_id,
        """
        const nodes=[...document.querySelectorAll('.notification.error,.toast.toast-error,[role="alert"]')];
        const viewport={w:window.innerWidth,h:window.innerHeight};
        return nodes
          .filter(n => {
            const style = window.getComputedStyle(n);
            const box = n.getBoundingClientRect();
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            if (box.height <= 0 || box.width <= 0) return false;
            return box.bottom >= 0 && box.right >= 0 && box.top <= viewport.h && box.left <= viewport.w;
          })
          .map(n => ({ text: (n.textContent || '').trim().slice(0, 500), route: location.pathname }))
          .filter(x => !!x.text)
          .slice(0, 20);
        """,
    ).get("value")
    if not isinstance(out, list):
        return []
    result: List[Dict[str, str]] = []
    for item in out:
        if not isinstance(item, dict):
            continue
        result.append({"text": str(item.get("text") or ""), "route": str(item.get("route") or "")})
    return result


def step_nav(session_id: str, route: str, settle_s: float = 1.5):
    js(
        session_id,
        """
        const href = arguments[0];
        const a = [...document.querySelectorAll('a[href]')]
          .find(x => x.getAttribute('href') === href);
        if (a) { a.click(); return true; }
        location.href = href;
        return false;
        """,
        [route],
    )
    time.sleep(settle_s)


def settle(extra_seconds: float):
    if extra_seconds > 0:
        time.sleep(extra_seconds)

