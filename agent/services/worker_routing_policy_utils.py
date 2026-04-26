from __future__ import annotations

from typing import Any

RESEARCH_SPECIALIZATIONS = ("deep_research", "repo_research", "document_research")


def normalize_capabilities(capabilities: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for cap in capabilities or []:
        value = str(cap or "").strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def extract_blueprint_role_defaults(task: dict | None) -> dict[str, Any]:
    raw = (task or {}).get("blueprint_role_defaults")
    if not isinstance(raw, dict):
        return {}
    capability_defaults = normalize_capabilities(raw.get("capability_defaults"))
    risk_profile = str(raw.get("risk_profile") or "").strip().lower()
    verification_defaults = raw.get("verification_defaults")
    normalized: dict[str, Any] = {}
    if capability_defaults:
        normalized["capability_defaults"] = capability_defaults
    if risk_profile in {"low", "balanced", "high", "strict"}:
        normalized["risk_profile"] = risk_profile
    if isinstance(verification_defaults, dict):
        normalized["verification_defaults"] = dict(verification_defaults)
    return normalized


def merge_capabilities_with_blueprint_defaults(
    base_capabilities: list[str] | None,
    task: dict | None,
) -> list[str]:
    merged = normalize_capabilities(base_capabilities)
    defaults = extract_blueprint_role_defaults(task)
    for capability in defaults.get("capability_defaults") or []:
        if capability not in merged:
            merged.append(capability)
    return merged


def derive_required_capabilities(task: dict | None, task_kind: str | None = None) -> list[str]:
    explicit = normalize_capabilities((task or {}).get("required_capabilities"))
    if explicit:
        return explicit
    kind = str(task_kind or (task or {}).get("task_kind") or "").strip().lower()
    if kind in {"planning", "research", "coding", "review", "testing", "verification"}:
        if kind == "research":
            text = " ".join(
                [
                    str((task or {}).get("title") or ""),
                    str((task or {}).get("description") or ""),
                ]
            ).lower()
            derived = ["research"]
            if any(
                token in text
                for token in (
                    "deep research",
                    "deep-dive",
                    "deep dive",
                    "comprehensive analysis",
                    "comprehensive report",
                )
            ):
                derived.append("deep_research")
            if any(token in text for token in ("repository", "repo", "codebase", "source tree", "git history")):
                derived.append("repo_research")
            if any(token in text for token in ("document", "pdf", "spec", "readme", "docs", "knowledge base")):
                derived.append("document_research")
            return derived
        return [kind]
    text = " ".join(
        [
            str((task or {}).get("title") or ""),
            str((task or {}).get("description") or ""),
        ]
    ).lower()
    if "test" in text or "verify" in text:
        return ["testing"]
    if "review" in text:
        return ["review"]
    if "plan" in text:
        return ["planning"]
    if "research" in text or "analy" in text:
        derived = ["research"]
        if any(token in text for token in ("repository", "repo", "codebase", "source tree", "git history")):
            derived.append("repo_research")
        if any(token in text for token in ("document", "pdf", "spec", "readme", "docs", "knowledge base")):
            derived.append("document_research")
        return derived
    return ["coding"]


def derive_research_specialization(
    task: dict | None,
    task_kind: str | None = None,
    required_capabilities: list[str] | None = None,
) -> str | None:
    kind = str(task_kind or (task or {}).get("task_kind") or "").strip().lower()
    normalized_required = normalize_capabilities(required_capabilities) or derive_required_capabilities(task, kind)
    if kind != "research" and "research" not in normalized_required:
        return None
    for specialization in RESEARCH_SPECIALIZATIONS:
        if specialization in normalized_required:
            return specialization
    return "research" if "research" in normalized_required else None
