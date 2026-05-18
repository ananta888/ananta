from __future__ import annotations

import re
from typing import Any


def classify_output_shape(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    lower = text.lower()
    detected: list[str] = []

    if text.startswith("[") and text.endswith("]"):
        detected.append("strict_json_array")
    if text.startswith("{") and text.endswith("}"):
        detected.append("strict_json_object")
    if lower.startswith("```json") or lower.startswith("```"):
        detected.append("json_in_markdown_fence")
    if "graph td" in lower or "graph lr" in lower:
        detected.append("mermaid_graph")
    if re.search(r"(?m)^\s*[-*]\s+", text):
        detected.append("markdown_bullets")
    if re.search(r"(?m)^\s*\d+\.\s+", text):
        detected.append("numbered_steps")
    if text.startswith("{") and "'" in text and '"' not in text:
        detected.append("python_literal")
    if "<" in text and "</" in text:
        detected.append("xml_like")
    if re.search(r"(?m)^\s*[a-zA-Z_]+:\s+", text):
        detected.append("yaml_like")
    if not detected:
        detected.append("freeform_prose")

    primary = detected[0]
    if "json_in_markdown_fence" in detected:
        primary = "json_in_markdown_fence"
    if "mermaid_graph" in detected:
        primary = "mermaid_graph"

    return {
        "primary_shape": primary,
        "detected_shapes": detected,
        "confidence": "high" if primary != "freeform_prose" else "low",
        "evidence": text[:240],
    }
