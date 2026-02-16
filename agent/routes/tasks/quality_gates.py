from typing import Any


DEFAULT_QUALITY_GATES = {
    "enabled": True,
    "autopilot_enforce": True,
    "coding_keywords": [
        "code",
        "implement",
        "fix",
        "refactor",
        "bug",
        "test",
        "feature",
        "endpoint",
    ],
    "required_output_markers_for_coding": [
        "test",
        "pytest",
        "passed",
        "success",
        "lint",
        "ok",
    ],
    "min_output_chars": 8,
}


def _normalize_policy(cfg: dict | None) -> dict:
    merged = dict(DEFAULT_QUALITY_GATES)
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            merged[k] = v
    return merged


def evaluate_quality_gates(task: Any, output: str | None, exit_code: int | None, policy: dict | None = None) -> tuple[bool, str]:
    cfg = _normalize_policy(policy)
    if not cfg.get("enabled", True):
        return True, "quality_gates_disabled"
    if exit_code not in (None, 0):
        return False, "non_zero_exit_code"

    safe_out = (output or "").strip()
    if len(safe_out) < int(cfg.get("min_output_chars", 8)):
        return False, "insufficient_output_evidence"

    title = str(getattr(task, "title", "") or "")
    desc = str(getattr(task, "description", "") or "")
    text = f"{title} {desc}".lower()
    coding_keywords = [str(x).lower() for x in (cfg.get("coding_keywords") or [])]
    is_coding_like = any(k and k in text for k in coding_keywords)
    if not is_coding_like:
        return True, "passed_generic"

    markers = [str(x).lower() for x in (cfg.get("required_output_markers_for_coding") or [])]
    out_l = safe_out.lower()
    if any(m and m in out_l for m in markers):
        return True, "passed_coding_markers"
    return False, "missing_coding_quality_markers"

