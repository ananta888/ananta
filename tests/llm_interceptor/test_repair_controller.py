from __future__ import annotations

from agent.services.llm_interceptor.repair_controller import RepairController


def test_repair_success_from_text_blob():
    repaired, reason = RepairController(max_attempts=1, enabled=True).repair_chat_completion(
        {"text": "hello"},
        model="intercepted-coder",
    )
    assert repaired is not None
    assert reason == "repaired"
    assert repaired["choices"][0]["message"]["content"] == "hello"


def test_repair_disabled():
    repaired, reason = RepairController(max_attempts=1, enabled=False).repair_chat_completion(
        {"text": "hello"},
        model="intercepted-coder",
    )
    assert repaired is None
    assert reason == "repair_disabled"


def test_repair_failure_invalid_json_string():
    repaired, reason = RepairController(max_attempts=1, enabled=True).repair_chat_completion(
        "{bad",
        model="intercepted-coder",
    )
    assert repaired is None
    assert reason == "repair_failed_invalid_json"

