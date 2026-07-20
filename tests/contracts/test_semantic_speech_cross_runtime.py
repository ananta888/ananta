from __future__ import annotations

import json
from pathlib import Path

import pytest

from ananta_contracts.semantic_speech import (
    SemanticSpeechContractError,
    validate_semantic_speech_transport_payload,
)

ROOT = Path(__file__).resolve().parents[2]


def test_semantic_speech_golden_cases_match_browser_validator() -> None:
    catalog = json.loads((ROOT / "tests/fixtures/webrtc/semantic_speech_transport_cases.json").read_text())
    context = catalog["context"]
    for case in catalog["cases"]:
        candidate = {**catalog["base"], **case.get("patch", {})}
        repeated = case.get("text_repeat")
        if repeated:
            candidate["text"] = repeated["value"] * repeated["count"]
        if case["expected"] == "accept":
            assert (
                validate_semantic_speech_transport_payload(candidate, **context, now_ms=catalog["now_ms"])["turn_id"]
                == "turn-a"
            )
        else:
            with pytest.raises(SemanticSpeechContractError) as raised:
                validate_semantic_speech_transport_payload(candidate, **context, now_ms=catalog["now_ms"])
            assert raised.value.reason_code == case["expected"], case["name"]
