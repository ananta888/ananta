"""DSL v2 Expression Engine — kein eval/exec, safe regex, fehlende Pfade → None/false."""
from __future__ import annotations

import re
import time
from typing import Any

_MAX_REGEX_LEN = 200


def _resolve_path(path: str, context: Any) -> Any:
    """Traversiert Pfad wie 'tui.snapshot.screen_hash' durch Context-Dict oder Dataclass.

    Convention: Strings mit '.' werden als Pfad behandelt.
    Strings ohne '.' werden zuerst als Pfad versucht (top-level key), dann als Literal.
    """
    parts = path.split(".")
    current: Any = context
    for part in parts:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None
    return current


def _resolve_operand(operand: Any, context: Any) -> Any:
    """Löst einen Operanden auf: Pfad oder Literal.

    Strings mit '.' werden immer als Pfad behandelt.
    Strings ohne '.' werden als Pfad versucht; wenn der Key im Context vorhanden ist,
    wird der aufgelöste Wert zurückgegeben. Sonst wird der String als Literal behandelt.
    """
    if not isinstance(operand, str):
        return operand
    if "." in operand:
        # Dotted path — always resolve
        return _resolve_path(operand, context)
    # Top-level key — try to resolve, fall back to literal
    if isinstance(context, dict) and operand in context:
        return context[operand]
    if not isinstance(context, dict) and hasattr(context, operand):
        return getattr(context, operand)
    # Not found as path — treat as literal string value
    return operand


def evaluate(expr: dict[str, Any], context: Any) -> bool | float | None:
    """Wertet DSL-Ausdruck aus. Fehlende Felder → None/false, nie Exception im UI-Pfad."""
    try:
        return _eval(expr, context)
    except Exception:
        return None


def _eval(expr: dict[str, Any], context: Any) -> bool | float | None:
    if "all" in expr:
        return all(bool(_eval(e, context)) for e in expr["all"])
    if "any" in expr:
        return any(bool(_eval(e, context)) for e in expr["any"])
    if "not" in expr:
        result = _eval(expr["not"], context)
        return not result if result is not None else None
    if "eq" in expr:
        a, b = expr["eq"]
        va = _resolve_operand(a, context)
        vb = _resolve_operand(b, context)
        return va == vb
    if "gt" in expr:
        a, b = expr["gt"]
        va = _resolve_operand(a, context)
        vb = _resolve_operand(b, context)
        if va is None or vb is None:
            return None
        return float(va) > float(vb)
    if "lt" in expr:
        a, b = expr["lt"]
        va = _resolve_operand(a, context)
        vb = _resolve_operand(b, context)
        if va is None or vb is None:
            return None
        return float(va) < float(vb)
    if "contains" in expr:
        container_path, value = expr["contains"]
        container = _resolve_operand(container_path, context)
        if container is None:
            return False
        return value in container
    if "regex_safe" in expr:
        pattern_str, target_path = expr["regex_safe"]
        if len(pattern_str) > _MAX_REGEX_LEN:
            return False
        target = _resolve_operand(target_path, context)
        if target is None:
            return False
        try:
            return bool(re.search(pattern_str[:_MAX_REGEX_LEN], str(target)))
        except re.error:
            return False
    if "changed_recently" in expr:
        spec = expr["changed_recently"]
        path = spec.get("path", "")
        within_seconds = float(spec.get("within_seconds", 5.0))
        val = _resolve_path(path + "_changed_at", context)
        if val is None:
            return False
        return (time.monotonic() - float(val)) <= within_seconds
    if "distance" in expr:
        spec = expr["distance"]
        ax = _resolve_path(spec.get("ax", ""), context)
        ay = _resolve_path(spec.get("ay", ""), context)
        bx = _resolve_path(spec.get("bx", ""), context)
        by_ = _resolve_path(spec.get("by", ""), context)
        if any(v is None for v in [ax, ay, bx, by_]):
            return None
        return float(abs(int(ax) - int(bx)) + abs(int(ay) - int(by_)))
    if "direction_towards" in expr:
        # Returns true if snake is generally moving toward target
        return None  # Not resolvable without live game state — graceful None
    if "intersects" in expr:
        spec = expr["intersects"]
        bbox_a = spec.get("a")
        bbox_b = spec.get("b")
        if bbox_a is None or bbox_b is None:
            return False
        def _rects_overlap(a: dict, b: dict) -> bool:
            return not (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"] or
                       a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])
        return _rects_overlap(bbox_a, bbox_b)
    return None
