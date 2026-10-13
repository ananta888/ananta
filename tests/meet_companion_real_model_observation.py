"""Opt-in actual LLM composition and observations for the installed Task gate."""

import json
import os

import pytest

from tests.meet_companion_ollama_fixture import CompanionOllamaFixture
from tests.meet_ollama_model_spec import MODEL_DIGEST


def real_model_scope(assigned, scenario):
    enabled = os.environ.get("MEET_COMPANION_INTEGRATED_REAL_LLM") == "1"
    withdrawal = os.environ.get("MEET_COMPANION_INTEGRATED_GPU_WITHDRAWAL") == "1"
    if enabled and (not assigned or scenario not in (
        {"answers-cancel", "speech-mid-speech"} if withdrawal else {"answers-cancel"}
    )):
        pytest.skip("explicit actual LLM gate requires an eligible assigned scenario")
    return enabled


def start_real_model(cleanup, network, state, *, enabled):
    if enabled:
        state["provider"] = cleanup.enter_context(CompanionOllamaFixture(network,
            os.environ["MEET_TEST_OLLAMA_MODELS"], os.environ["MEET_TEST_WSL_DRIVER_STORE"]))


def expected_unique_replies(result, *, actual):
    if actual:
        assert type(result["unique"]) is int and 1 <= result["unique"] <= 3
        return result["unique"]
    return 3


def record_real_model(state, counts, record_property, *, enabled):
    if enabled:
        resident = state["provider"].request("/api/ps")["models"]
        assert len(resident) == 1 and resident[0]["digest"] == MODEL_DIGEST and resident[0]["size_vram"] > 0
        record_property("actual_installed_llm", json.dumps({"model_digest": MODEL_DIGEST,
            "provider_requests": counts["provider"], "tool_requests": counts["tool"],
            "size_vram": resident[0]["size_vram"], "production_release_evidence": False}, sort_keys=True))
