import json
from pathlib import Path

from agent.services.text_quality.criteria_service import CriteriaService
from agent.services.text_quality.deterministic_scanner import (
    DeterministicTextQualityScanner,
)
from agent.services.text_quality.models import ContentKind, TextQualityEvaluationRequest


def _scan(text: str, *, language: str = "de", kind=ContentKind.FREEFORM_PROSE):
    criteria = CriteriaService().default(language, kind)
    return DeterministicTextQualityScanner().analyze(
        TextQualityEvaluationRequest(
            text=text,
            language=language,
            content_kind=kind,
            criteria=criteria,
        )
    )


def test_scanner_is_unicode_case_insensitive_and_offsets_are_bounded():
    result = _scan(
        "IN DER HEUTIGEN ZEIT ist Präzision für belastbare Entscheidungen wichtig. "
        "Darüber hinaus folgt eine konkrete Erklärung mit 25 Prozent."
    )
    assert "generic_phrase" in result.reason_codes
    assert all(0 <= finding.start <= finding.end for finding in result.findings)
    assert all(len(finding.excerpt) <= 160 for finding in result.findings)


def test_short_text_is_unscorable_and_technical_content_avoids_prose_transition_rule():
    assert _scan("Kurz und konkret.").status.value == "unscorable"
    result = _scan(
        "Darüber hinaus setzt der Dienst den Timeout auf 30 Sekunden und "
        "liefert bei Status 503 einen stabilen Fehlercode an den Aufrufer.",
        kind=ContentKind.TECHNICAL_DOCUMENTATION,
    )
    assert "overused_transition" not in result.reason_codes


def test_language_profiles_are_independent_and_versioned():
    root = Path("agent/services/text_quality/profiles")
    de = json.loads((root / "de.json").read_text(encoding="utf-8"))
    en = json.loads((root / "en.json").read_text(encoding="utf-8"))
    assert de["version"] and en["version"]
    assert de["profile_name"] != en["profile_name"]
    assert "in today's world" not in de["blocked_phrases"]
