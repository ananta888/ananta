from __future__ import annotations

import re
from typing import Any


def analyze_format_errors(raw_text: str, parse_result: dict[str, Any] | None = None) -> list[str]:
    text = str(raw_text or "")
    errors: list[str] = []
    trimmed = text.strip()
    if trimmed.startswith("```"):
        errors.append("markdown_fences")
    if "\n{" in text and not trimmed.startswith("{") and not trimmed.startswith("["):
        errors.append("preface_text_before_json")
    if re.search(r",\s*[}\]]", text):
        errors.append("trailing_commas")
    if "'" in text and '"' not in text and ("{" in text or "[" in text):
        errors.append("single_quotes")
    if re.search(r"(?m)^\s*[A-Za-z_][A-Za-z0-9_]*\s*:", text) and '"' not in text:
        errors.append("unquoted_keys")
    if isinstance(parse_result, dict):
        if parse_result.get("missing_required_fields"):
            errors.append("missing_required_fields")
        if parse_result.get("depends_on_wrong_type"):
            errors.append("depends_on_wrong_type")
    return list(dict.fromkeys(errors))
