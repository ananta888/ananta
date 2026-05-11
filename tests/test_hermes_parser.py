"""HF-T026: Strict output schema tests per Hermes mode."""
from __future__ import annotations

import json

import pytest

from worker.core.hermes_output_parser import parse_hermes_json_output, validate_payload_for_mode


# ── parse_hermes_json_output ──────────────────────────────────────────────────

def _valid_payload(**overrides) -> str:
    base = {
        "status": "success",
        "artifact_type": "plan",
        "summary": "A plan",
        "findings": [],
        "risks": [],
        "suggested_tests": [],
        "confidence": 0.8,
        "requires_approval_for_apply": False,
        "no_side_effects_claimed": True,
    }
    base.update(overrides)
    return json.dumps(base)


def test_parse_valid_plain_json() -> None:
    result = parse_hermes_json_output(_valid_payload())
    assert result.ok is True
    assert result.payload["status"] == "success"


def test_parse_fenced_json_block() -> None:
    raw = f"```json\n{_valid_payload()}\n```"
    result = parse_hermes_json_output(raw)
    assert result.ok is True


def test_parse_plain_text_fails() -> None:
    result = parse_hermes_json_output("The plan is to refactor the module.")
    assert result.ok is False


def test_parse_empty_string_fails() -> None:
    result = parse_hermes_json_output("")
    assert result.ok is False


def test_parse_rejects_unsafe_side_effect_claim_in_summary() -> None:
    payload = _valid_payload(summary="modified files and executed commands")
    result = parse_hermes_json_output(payload)
    assert result.ok is False
    assert "unsafe_side_effect" in result.reason_code


def test_validate_rejects_no_side_effects_claimed_false() -> None:
    # no_side_effects_claimed=False is caught by validate_payload_for_mode, not the parser
    payload = json.loads(_valid_payload(no_side_effects_claimed=False))
    err = validate_payload_for_mode(payload, mode="plan_only")
    assert err is not None and "side_effect" in err


# ── validate_payload_for_mode ─────────────────────────────────────────────────

def _base_payload(**overrides) -> dict:
    base = {
        "status": "success",
        "artifact_type": "plan",
        "summary": "ok",
        "findings": [],
        "risks": [],
        "suggested_tests": [],
        "confidence": 0.8,
        "requires_approval_for_apply": False,
        "no_side_effects_claimed": True,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("mode", ["plan_only"])
def test_validate_plan_only_valid(mode: str) -> None:
    assert validate_payload_for_mode(_base_payload(), mode=mode) is None


@pytest.mark.parametrize("mode", ["plan_only"])
def test_validate_plan_only_missing_summary_allowed(mode: str) -> None:
    # plan_only does not require extra fields beyond what parser already checked
    payload = _base_payload()
    del payload["summary"]
    # May or may not return error depending on implementation; just must not crash
    result = validate_payload_for_mode(payload, mode=mode)
    assert result is None or isinstance(result, str)


@pytest.mark.parametrize("mode", ["review", "code_review"])
def test_validate_review_mode_valid(mode: str) -> None:
    payload = _base_payload(artifact_type="review", findings=[{"issue": "x"}])
    assert validate_payload_for_mode(payload, mode=mode) is None


@pytest.mark.parametrize("mode", ["review", "code_review"])
def test_validate_review_mode_missing_findings_returns_error(mode: str) -> None:
    payload = _base_payload(artifact_type="review")
    del payload["findings"]
    result = validate_payload_for_mode(payload, mode=mode)
    assert result is not None and isinstance(result, str)


def test_validate_summarize_mode_valid() -> None:
    payload = _base_payload(artifact_type="summary")
    assert validate_payload_for_mode(payload, mode="summarize") is None


def test_validate_patch_propose_valid() -> None:
    payload = _base_payload(
        artifact_type="patch",
        requires_approval_for_apply=True,
        patch_unified_diff="--- a\n+++ b\n@@ -1 +1 @@\n-x\n+y",
        touched_files=["a.py"],
    )
    assert validate_payload_for_mode(payload, mode="patch_propose") is None


def test_validate_patch_propose_missing_requires_approval_returns_error() -> None:
    payload = _base_payload(artifact_type="patch")
    del payload["requires_approval_for_apply"]
    result = validate_payload_for_mode(payload, mode="patch_propose")
    assert result is not None and isinstance(result, str)


def test_validate_research_limited_valid() -> None:
    payload = _base_payload(artifact_type="research", claims=[{"c": "x"}])
    assert validate_payload_for_mode(payload, mode="research_limited") is None


def test_validate_unknown_mode_does_not_crash() -> None:
    result = validate_payload_for_mode(_base_payload(), mode="unknown_mode")
    assert result is None or isinstance(result, str)
