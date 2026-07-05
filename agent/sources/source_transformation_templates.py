from __future__ import annotations

from typing import Any

_TEMPLATES: tuple[dict[str, Any], ...] = tuple(
    {
        "id": template_id,
        "title": title,
        "prompt_intent": prompt_intent,
        "output_schema_ref": "schemas/sources/source_transformation_output.v1",
        "default_enabled": default_enabled,
        "allowed_source_types": ["open_notebook"],
    }
    for template_id, title, prompt_intent, default_enabled in (
        ("api_contracts", "API Contracts", "Extract explicit API contracts and their constraints.", False),
        ("architecture_facts", "Architecture Facts", "Extract verifiable architecture facts.", True),
        ("citation_candidates", "Citation Candidates", "Identify claims that require direct source citations.", True),
        ("key_terms", "Key Terms", "Extract key terms with concise grounded definitions.", True),
        ("security_notes", "Security Notes", "Extract security-relevant facts and stated mitigations.", False),
        ("summary", "Summary", "Produce a concise source-grounded summary.", True),
        ("testable_claims", "Testable Claims", "Extract falsifiable claims and possible verification steps.", True),
    )
)


def list_source_transformation_templates() -> list[dict[str, Any]]:
    return [dict(item) for item in _TEMPLATES]


def get_source_transformation_template(template_id: str) -> dict[str, Any] | None:
    normalized = str(template_id or "").strip()
    return next((dict(item) for item in _TEMPLATES if item["id"] == normalized), None)
