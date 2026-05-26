from __future__ import annotations

from agent.services.helpcenter_analyzer_service import (
    analyze_helpcenter_message,
    build_helpcenter_analysis_prompt,
    parse_llm_analysis_response,
)
from agent.services.helpcenter_manual_input_service import create_manual_helpcenter_message


def _message() -> dict:
    return create_manual_helpcenter_message(
        title="CI failed",
        text="pytest failed due to assertion",
        severity="error",
        source_ref="manual://ci/failure-1",
    )


def test_analyzer_returns_analysis_without_auto_fix() -> None:
    analysis = analyze_helpcenter_message(_message(), log_text="FAILURES\nAssertionError")
    assert analysis["status"] == "ready"
    assert analysis["no_auto_fix"] is True
    assert any(item.get("reason_code") == "pytest_failure" for item in analysis["machine_readable_findings"])


def test_analyzer_prompt_contract_is_json_with_no_fix_rule() -> None:
    prompt = build_helpcenter_analysis_prompt(message=_message(), log_excerpt_lines=["line"])
    assert "no automatic fix actions" in prompt
    assert "response_schema" in prompt


def test_llm_invalid_response_returns_degraded_analysis() -> None:
    degraded = parse_llm_analysis_response("not-json", fallback_message=_message())
    assert degraded["status"] == "degraded"
    assert degraded["no_auto_fix"] is True
    assert any(item.get("reason_code") == "llm_analysis_invalid" for item in degraded["machine_readable_findings"])
