"""Unit tests for PromptTraceService, Storage, and model. PTI-027."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time

import pytest


@pytest.fixture
def tmp_svc(tmp_path):
    from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
    storage = PromptTraceStorage(data_dir=str(tmp_path))
    return PromptTraceService(storage=storage)


class TestPromptTraceModel:
    def test_create_trace_for_plain_prompt(self, tmp_svc):
        trace = tmp_svc.create_trace(
            provider="lmstudio",
            model="gemma",
            prompt="Hello world",
            request_kind="generate",
        )
        assert trace.trace_id
        assert trace.provider == "lmstudio"
        assert trace.model == "gemma"
        assert trace.prompt_hash_sha256 is not None
        assert "Hello world" in (trace.final_prompt_redacted or "")
        assert trace.success is None  # not yet finalized

    def test_create_trace_for_chat_messages(self, tmp_svc):
        messages = [
            {"role": "system", "content": "You are a planner."},
            {"role": "user", "content": "Plan a project."},
        ]
        trace = tmp_svc.create_trace(
            provider="ollama",
            model="llama3",
            messages=messages,
            request_kind="planning",
        )
        assert trace.messages_redacted is not None
        assert len(trace.messages_redacted) == 2
        assert trace.messages_redacted[0]["role"] == "system"
        assert trace.prompt_hash_sha256 is not None

    def test_prompt_hash_stable(self, tmp_svc):
        prompt = "Stable prompt text"
        t1 = tmp_svc.create_trace(prompt=prompt)
        t2 = tmp_svc.create_trace(prompt=prompt)
        assert t1.prompt_hash_sha256 == t2.prompt_hash_sha256

    def test_redacts_api_keys_and_bearer_tokens(self, tmp_svc):
        trace = tmp_svc.create_trace(
            prompt="Call with Authorization: Bearer sk-abc123 and OPENAI_API_KEY=my-key",
        )
        assert "sk-abc123" not in (trace.final_prompt_redacted or "")
        assert "my-key" not in (trace.final_prompt_redacted or "")
        assert trace.secrets_detected > 0

    def test_finalize_sets_success_and_latency(self, tmp_svc):
        trace = tmp_svc.create_trace(provider="lmstudio", model="m", prompt="p")
        finalized = tmp_svc.finalize_trace(trace, success=True, response_text="result", usage={"total_tokens": 42})
        assert finalized.success is True
        assert finalized.latency_ms is not None
        assert finalized.latency_ms >= 0
        assert finalized.usage == {"total_tokens": 42}
        assert finalized.response_hash_sha256 is not None

    def test_finalize_failure_sets_error(self, tmp_svc):
        trace = tmp_svc.create_trace(prompt="p")
        finalized = tmp_svc.finalize_trace(trace, success=False, error_type="timeout", error_message="timed out")
        assert finalized.success is False
        assert finalized.error_type == "timeout"

    def test_to_dict_roundtrip(self, tmp_svc):
        from agent.services.prompt_trace_service import PromptTrace
        trace = tmp_svc.create_trace(provider="lmstudio", model="m", prompt="round trip")
        d = trace.to_dict()
        restored = PromptTrace.from_dict(d)
        assert restored.trace_id == trace.trace_id
        assert restored.prompt_hash_sha256 == trace.prompt_hash_sha256


class TestPromptTraceStorage:
    def test_jsonl_storage_append_and_get(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        trace = svc.create_trace(provider="ollama", model="llama3", prompt="store test")
        finalized = svc.finalize_trace(trace, success=True, response_text="ok")
        svc.store(finalized)

        retrieved = storage.get_by_trace_id(trace.trace_id)
        assert retrieved is not None
        assert retrieved.trace_id == trace.trace_id
        assert retrieved.success is True

    def test_list_returns_newest_first(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        for i in range(3):
            trace = svc.create_trace(prompt=f"prompt {i}")
            finalized = svc.finalize_trace(trace, success=True)
            svc.store(finalized)
            time.sleep(0.01)

        traces = storage.list(limit=10)
        assert len(traces) == 3
        # newest first
        assert traces[0].created_at >= traces[1].created_at

    def test_corrupted_jsonl_line_is_skipped(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        import os
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        trace = svc.create_trace(prompt="good trace")
        finalized = svc.finalize_trace(trace, success=True)
        svc.store(finalized)

        # inject corrupted line
        path = os.path.join(str(tmp_path), "prompt_traces.jsonl")
        with open(path, "a") as f:
            f.write("{not valid json\n")

        traces = storage.list(limit=10)
        assert len(traces) == 1  # corrupt line skipped

    def test_raw_prompt_not_stored_by_default(self, tmp_svc):
        trace = tmp_svc.create_trace(prompt="sensitive info")
        assert not trace.raw_available

    def test_find_by_goal_id(self, tmp_path):
        from agent.services.prompt_trace_service import PromptTraceService, PromptTraceStorage
        storage = PromptTraceStorage(data_dir=str(tmp_path))
        svc = PromptTraceService(storage=storage)

        for i in range(3):
            trace = svc.create_trace(goal_id="goal-abc" if i < 2 else "goal-xyz", prompt=f"p{i}")
            svc.store(svc.finalize_trace(trace, success=True))

        goal_traces = storage.find_by_goal_id("goal-abc")
        assert len(goal_traces) == 2
        other_traces = storage.find_by_goal_id("goal-xyz")
        assert len(other_traces) == 1


class TestPromptHash:
    def test_hash_is_sha256(self):
        from agent.services.prompt_trace_service import prompt_hash
        text = "hello"
        expected = hashlib.sha256(text.encode()).hexdigest()
        assert prompt_hash(text) == expected

    def test_hash_none_returns_none(self):
        from agent.services.prompt_trace_service import prompt_hash
        assert prompt_hash(None) is None

    def test_hash_empty_returns_none(self):
        from agent.services.prompt_trace_service import prompt_hash
        assert prompt_hash("") is None
