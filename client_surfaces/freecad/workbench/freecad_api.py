from __future__ import annotations

from typing import Any


def _read_attr_or_key(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(value, dict) and name in value:
            return value.get(name)
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def extract_document_payload(document: Any) -> dict[str, Any]:
    return {
        "name": str(_read_attr_or_key(document, "Name", "Label", "name", default="Untitled") or "Untitled"),
        "unit": str(_read_attr_or_key(document, "UnitSystem", "unit", default="") or ""),
        "path": str(_read_attr_or_key(document, "FileName", "path", default="") or ""),
    }


def extract_object_payload(obj: Any) -> dict[str, Any]:
    view_object = _read_attr_or_key(obj, "ViewObject", "view_object", default=None)
    shape = _read_attr_or_key(obj, "Shape", "shape", default=None)
    return {
        "name": str(_read_attr_or_key(obj, "Label", "Name", "name", default="") or ""),
        "type": str(_read_attr_or_key(obj, "TypeId", "type", default="Unknown") or "Unknown"),
        "visibility": bool(_read_attr_or_key(view_object, "Visibility", "visibility", default=True)),
        "volume": _safe_float(_read_attr_or_key(shape, "Volume", "volume", default=0.0)),
    }


def extract_selection_names(selection_objects: list[Any] | None) -> list[str]:
    names: list[str] = []
    for item in list(selection_objects or []):
        value = str(_read_attr_or_key(item, "Label", "Name", "name", default="") or "").strip()
        if value:
            names.append(value)
    return names


def extract_constraints(objects: list[Any] | None) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    for obj in list(objects or []):
        raw_constraints = _read_attr_or_key(obj, "Constraints", "constraints", default=[]) or []
        for constraint in list(raw_constraints)[:32]:
            constraints.append(
                {
                    "name": str(_read_attr_or_key(constraint, "Name", "name", default="") or ""),
                    "type": str(_read_attr_or_key(constraint, "Type", "type", default="Constraint") or "Constraint"),
                    "status": str(_read_attr_or_key(constraint, "Status", "status", default="unknown") or "unknown"),
                }
            )
    return constraints[:32]


def capture_runtime_snapshot(*, app_module: Any, gui_module: Any | None = None) -> dict[str, Any]:
    document = _read_attr_or_key(app_module, "ActiveDocument", default=None)
    objects = list(_read_attr_or_key(document, "Objects", "objects", default=[]) or [])
    selection_provider = _read_attr_or_key(gui_module, "Selection", default=None)
    if selection_provider is not None and hasattr(selection_provider, "getSelection"):
        selection_objects = list(selection_provider.getSelection() or [])
    else:
        selection_objects = []
    return {
        "document": extract_document_payload(document),
        "objects": [extract_object_payload(item) for item in objects],
        "selection": extract_selection_names(selection_objects),
        "constraints": extract_constraints(objects),
    }
