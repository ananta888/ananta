from __future__ import annotations

from agent.providers.lc_lg import LangChainProviderConfig
from worker.adapters.langchain_adapter import LangChainAdapter
from worker.adapters.workflow_adapter_base import WorkflowArtifactResult


def test_stream_never_builds_or_calls_a_direct_langchain_chat_model(monkeypatch) -> None:
    adapter = LangChainAdapter(
        LangChainProviderConfig(
            enabled=True,
            mode="local_live",
            model_provider_ref="local.default",
            retriever_source="none",
            external_calls_allowed=False,
        )
    )
    called = []

    def governed_execute(**kwargs):
        called.append(kwargs)
        return WorkflowArtifactResult(
            adapter_id="adapter.langchain",
            task_id=kwargs["task_id"],
            task_type=kwargs["task_type"],
            status="success",
            summary="governed",
            artifacts=[{"artifact_id": "artifact-1", "content": "safe output"}],
        )

    monkeypatch.setattr(adapter, "execute", governed_execute)
    monkeypatch.setattr(adapter, "_langchain_available", lambda: True)

    frames = list(
        adapter.stream(
            task_id="task-1",
            task_type="summarize",
            payload={"prompt": "redacted by middleware"},
        )
    )

    assert len(called) == 1
    assert frames[0] == {
        "adapter_id": "adapter.langchain",
        "task_id": "task-1",
        "event_type": "token",
        "token": "safe output",
        "source": "provider_middleware_validated_artifact",
    }
    assert frames[-1]["event_type"] == "stream_end"
    assert frames[-1]["result"]["status"] == "success"
