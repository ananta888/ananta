from __future__ import annotations

from pathlib import Path

import jsonschema
import pytest

from agent.services.codecompass_agentic_retrieval_contract import (
    REASON_EMPTY_SCOPE,
    REASON_INVALID_SIGNALS,
    REASON_NO_RESULT,
    REASON_QUERY_REQUIRED,
    REASON_SCOPE_WIDENING,
    REASON_UNKNOWN_MODE,
    REASON_VECTOR_FAIL_CLOSED,
    REASON_VECTOR_STALE,
    REASON_VECTOR_UNAVAILABLE,
    SCHEMA_ID,
    map_vector_backend_reason,
)
from agent.services.codecompass_agentic_retrieval_planner import (
    CodeCompassAgenticRetrievalPlanner,
)
from agent.services.codecompass_agentic_retrieval_service import (
    CodeCompassAgenticRetrievalService,
)
from agent.services.codecompass_retrieval_capability_service import (
    bind_retrieval_capability,
)
from agent.services.codecompass_context_planner_service import CodeCompassContextPlanner
from worker.retrieval.vector_store_contract import VectorStoreFailClosedError

def _schema() -> dict:
    return __import__("json").loads(Path("schemas/codecompass.agentic-retrieval.v1.json").read_text())


def _validate(payload: dict) -> None:
    jsonschema.validate(payload, _schema())


def _service(**kwargs) -> CodeCompassAgenticRetrievalService:
    class _CapabilityBoundService(CodeCompassAgenticRetrievalService):
        def retrieve(self, request, *, capability=None):
            return super().retrieve(
                request,
                capability=_capability() if capability is None else capability,
            )

        def retrieve_from_tool_args(self, arguments, *, capability=None):
            return super().retrieve_from_tool_args(
                arguments,
                capability=_capability() if capability is None else capability,
            )

    return _CapabilityBoundService(**kwargs)


def _request(**overrides) -> dict:
    payload = {
        "schema": SCHEMA_ID,
        "kind": "request",
        "query": "PaymentService retry_timeout",
        "mode": "hybrid",
        "task_kind": "bugfix",
        "budget": {"top_k": 4, "max_chars": 2000, "candidate_limit": 8},
    }
    payload.update(overrides)
    return payload


def _capability(**overrides) -> dict:
    payload = {
        "tenant_id": "t1",
        "workspace_id": "ws-1",
        "repository_id": "repo-1",
        "source_scope": "repo-1",
        "revision": "rev-abc",
        "allowed_paths": ["src"],
    }
    payload.update(overrides)
    subject_id = str(payload.pop("subject_id", "principal-1"))
    return bind_retrieval_capability(
        payload,
        subject_id=subject_id,
        now_epoch=1_700_000_000,
        ttl_seconds=2_000_000_000,
    )


def test_missing_server_capability_fails_closed() -> None:
    result = CodeCompassAgenticRetrievalService().retrieve(_request())
    assert result["status"] == "error"
    assert result["reason_code"] == REASON_EMPTY_SCOPE


def test_schema_rejects_backend_collection_fields() -> None:
    raw = __import__("json").loads(Path("schemas/codecompass.agentic-retrieval.v1.json").read_text())
    properties = raw["$defs"]["request"]["properties"]
    assert "collection" not in properties
    assert "qdrant" not in properties
    assert "api_key" not in properties


def test_unknown_mode_is_typed_error() -> None:
    result = _service().retrieve(_request(mode="cypher"))
    assert result["status"] == "error"
    assert result["reason_code"] == REASON_UNKNOWN_MODE
    _validate(result)


def test_empty_query_is_typed_error() -> None:
    result = _service().retrieve(_request(query="   "))
    assert result["status"] == "error"
    assert result["reason_code"] == REASON_QUERY_REQUIRED


def test_planner_selects_exact_for_symbol_query() -> None:
    plan = CodeCompassAgenticRetrievalPlanner().plan(query="PaymentService.retry_timeout()")
    assert "exact" in plan["signals"]
    assert plan["mode"] in {"exact", "hybrid"}
    assert plan["rationale"] == "symbol_or_path_query"


def test_planner_can_force_vector_only() -> None:
    plan = CodeCompassAgenticRetrievalPlanner().plan(query="retry policy", mode="vector")
    assert plan["signals"] == ["vector"]
    assert plan["mode"] == "vector"


def test_planner_does_not_widen_requested_signals() -> None:
    with pytest.raises(Exception) as exc:
        CodeCompassAgenticRetrievalPlanner().plan(
            query="architecture of billing",
            mode="auto",
            requested_signals=["vector"],
            allowed_signals=["exact"],
        )
    assert exc.value.reason == REASON_INVALID_SIGNALS


def test_context_planner_delegates_agentic_plan() -> None:
    plan = CodeCompassContextPlanner().plan_agentic_retrieval(
        query="src/payment_service.py",
        mode="auto",
    )
    assert plan["signals"]
    assert "exact" in plan["signals"]


def test_hybrid_keeps_exact_hit_ahead_of_stronger_vector_hit() -> None:
    def exact_search(query, **_kwargs):
        return [
            {
                "id": "exact-pay",
                "path": "src/payment_service.py",
                "content": "class PaymentService:\n    def retry_timeout(self): pass",
                "score": 0.55,
                "symbol": "PaymentService",
                "kind": "python_class",
            }
        ]

    def vector_search(query, **_kwargs):
        return [
            {
                "id": "vec-other",
                "path": "docs/payments-overview.md",
                "content": "generic payment processing narrative",
                "score": 0.99,
                "kind": "doc",
            }
        ]

    result = _service(exact_search=exact_search, vector_search=vector_search, graph_search=lambda *a, **k: []).retrieve(
        _request()
    )
    assert result["evidence"]
    assert result["evidence"][0]["path"] == "src/payment_service.py"
    assert result["evidence"][0]["signal_type"] == "exact"
    _validate(result)


def test_hybrid_preserves_one_successful_graph_result() -> None:
    result = _service(
        exact_search=lambda *_args, **_kwargs: [{
            "id": "exact",
            "path": "src/payment.py",
            "content": "PaymentService",
            "score": 10.0,
        }],
        vector_search=lambda *_args, **_kwargs: [],
        graph_search=lambda *_args, **_kwargs: [
            {
                "id": "duplicate",
                "path": "src/payment.py",
                "content": "PaymentService graph node",
                "score": 1.0,
            },
            {
                "id": "graph",
                "path": "src/payment_graph.py",
                "content": "PaymentService calls RetryPolicy",
                "score": 0.1,
            },
        ],
    ).retrieve(_request())

    assert result["evidence"][0]["path"] == "src/payment.py"
    assert any("graph" in row["signals"] for row in result["evidence"])


def test_duplicate_path_is_deduplicated_with_cross_engine_signals() -> None:
    def exact_search(query, **_kwargs):
        return [
            {
                "id": "same",
                "path": "src/payment.py",
                "content": "class PaymentService: pass",
                "score": 0.7,
            }
        ]

    def vector_search(query, **_kwargs):
        return [
            {
                "id": "same",
                "path": "src/payment.py",
                "content": "payment retry vector description",
                "score": 0.95,
            }
        ]

    result = _service(exact_search=exact_search, vector_search=vector_search, graph_search=lambda *a, **k: []).retrieve(
        _request()
    )
    paths = [item["path"] for item in result["evidence"]]
    assert paths.count("src/payment.py") == 1
    assert set(result["evidence"][0]["signals"]) >= {"exact", "vector"}
    assert result["diagnostics"]["dedup"]["output_count"] == 1


def test_bound_capability_is_fail_closed_on_empty_paths() -> None:
    result = _service().retrieve(_request(), capability=_capability(allowed_paths=[]))
    assert result["status"] == "error"
    assert result["reason_code"] == REASON_EMPTY_SCOPE


def test_request_cannot_widen_workspace_or_paths() -> None:
    service = _service(
        exact_search=lambda *a, **k: [
            {"id": "other", "path": "secret/other.py", "content": "nope", "score": 1.0}
        ]
    )
    widened = service.retrieve(
        _request(scope={"workspace_id": "ws-other", "allowed_paths": ["secret"]}),
        capability=_capability(),
    )
    assert widened["reason_code"] == REASON_SCOPE_WIDENING

    leaked = service.retrieve(
        _request(mode="exact"),
        capability=_capability(),
    )
    assert leaked["evidence"] == []
    assert leaked["reason_code"] in {REASON_NO_RESULT, "channel_empty"} or leaked["status"] in {
        "empty",
        "degraded",
    }


def test_hits_outside_allowed_paths_are_dropped() -> None:
    def exact_search(query, **_kwargs):
        return [
            {"id": "in", "path": "src/ok.py", "content": "ok", "score": 0.8},
            {"id": "out", "path": "secret/x.py", "content": "nope", "score": 0.9},
        ]

    result = _service(exact_search=exact_search).retrieve(
        _request(mode="exact"),
        capability=_capability(),
    )
    assert [item["path"] for item in result["evidence"]] == ["src/ok.py"]


def test_budget_truncates_and_sets_continuation() -> None:
    def exact_search(query, **_kwargs):
        return [
            {"id": f"row-{idx}", "path": f"src/f{idx}.py", "content": "x" * 80, "score": 0.9 - idx * 0.01}
            for idx in range(6)
        ]

    result = _service(exact_search=exact_search).retrieve(
        _request(mode="exact", budget={"top_k": 2, "max_chars": 2000, "candidate_limit": 8})
    )
    assert len(result["evidence"]) == 2
    assert result["truncated"] is True
    assert result["continuation_handle"]
    assert result["status"] == "degraded"


def test_max_tokens_is_a_hard_budget() -> None:
    service = _service(
        exact_search=lambda query, **_kwargs: [
            {"id": "large", "path": "src/large.py", "content": "x" * 2000, "score": 1.0}
        ],
        token_estimator=len,
    )
    result = service.retrieve(
        _request(mode="exact", budget={"top_k": 4, "max_chars": 32000, "max_tokens": 500, "candidate_limit": 8})
    )
    assert result["diagnostics"]["budget"]["used_tokens"] <= 500
    assert result["diagnostics"]["budget"]["used_chars"] <= 32000
    assert result["diagnostics"]["budget"]["truncation_reason"] == "max_tokens"


def test_continuation_is_query_scope_and_revision_bound() -> None:
    hits = [
        {"id": f"row-{index}", "path": f"src/f{index}.py", "content": "x", "score": 1.0}
        for index in range(4)
    ]
    service = _service(exact_search=lambda query, **_kwargs: hits, continuation_secret=b"x" * 32)
    first = service.retrieve(_request(mode="exact", budget={"top_k": 1, "max_chars": 1000, "candidate_limit": 8}))
    handle = first["continuation_handle"]
    changed_query = service.retrieve(
        _request(query="different", mode="exact", continuation_handle=handle, budget={"top_k": 1, "max_chars": 1000, "candidate_limit": 8})
    )
    changed_revision = service.retrieve(
        _request(mode="exact", continuation_handle=handle, budget={"top_k": 1, "max_chars": 1000, "candidate_limit": 8}),
        capability=_capability(revision="rev-other"),
    )
    assert changed_query["reason_code"] == "invalid_continuation_handle"
    assert changed_revision["reason_code"] == "invalid_continuation_handle"


def test_tampered_continuation_fails_closed() -> None:
    hits = [
        {"id": "a", "path": "src/a.py", "content": "x", "score": 1.0},
        {"id": "b", "path": "src/b.py", "content": "x", "score": 0.9},
    ]
    service = _service(exact_search=lambda query, **_kwargs: hits, continuation_secret=b"y" * 32)
    first = service.retrieve(_request(mode="exact", budget={"top_k": 1, "max_chars": 1000, "candidate_limit": 8}))
    handle = first["continuation_handle"]
    tampered = handle[:-1] + ("0" if handle[-1] != "0" else "1")
    result = service.retrieve(_request(mode="exact", continuation_handle=tampered, budget={"top_k": 1, "max_chars": 1000, "candidate_limit": 8}))
    assert result["reason_code"] == "invalid_continuation_handle"


def test_vector_unavailable_degrades_to_exact() -> None:
    def exact_search(query, **_kwargs):
        return [{"id": "exact", "path": "src/ok.py", "content": "ok", "score": 0.8}]

    def vector_search(query, **_kwargs):
        raise RuntimeError("qdrant_unavailable")

    result = _service(
        exact_search=exact_search,
        vector_search=vector_search,
        graph_search=lambda *a, **k: [],
    ).retrieve(_request())
    assert result["status"] == "degraded"
    assert result["reason_code"] == REASON_VECTOR_UNAVAILABLE
    assert result["evidence"][0]["path"] == "src/ok.py"
    assert result["diagnostics"]["engines"]["vector"]["status"] == "degraded"


def test_vector_fail_closed_does_not_search_globally() -> None:
    def vector_search(query, **_kwargs):
        raise VectorStoreFailClosedError("qdrant_unauthorized")

    result = _service(
        exact_search=lambda *a, **k: [{"id": "x", "path": "src/x.py", "content": "x", "score": 1.0}],
        vector_search=vector_search,
    ).retrieve(_request(mode="vector"))
    assert result["status"] == "error"
    assert result["reason_code"] == REASON_VECTOR_FAIL_CLOSED
    assert result["evidence"] == []


def test_stale_index_skips_vector_engine() -> None:
    result = _service(
        exact_search=lambda *a, **k: [{"id": "e", "path": "src/a.py", "content": "a", "score": 0.5}],
        vector_search=lambda *a, **k: [{"id": "v", "path": "src/b.py", "content": "b", "score": 0.9}],
        index_state=lambda: {"status": "stale", "manifest_hash": "abc", "model": "local_hash"},
    ).retrieve(_request())
    assert result["diagnostics"]["engines"]["vector"]["reason"] == REASON_VECTOR_STALE
    assert all(item["path"] != "src/b.py" for item in result["evidence"])


def test_backend_reason_mapping_hides_qdrant() -> None:
    assert map_vector_backend_reason("qdrant_timeout") == "vector_timeout"
    assert map_vector_backend_reason("dimensions_mismatch") == "vector_dimensions_mismatch"
    assert "qdrant" not in map_vector_backend_reason("qdrant_unavailable")


def test_architecture_consumer_uses_shared_service(monkeypatch) -> None:
    from agent.services import codecompass_architecture_retrieval as consumer

    service = _service(
        exact_search=lambda query, **_kwargs: [
            {"id": "mod", "path": "src/billing/service.py", "content": "billing subsystem", "score": 0.7}
        ],
        graph_search=lambda query, **_kwargs: [
            {"id": "sys", "path": "src/billing/service.py", "content": "BillingSystem", "score": 0.6}
        ],
        vector_search=lambda query, **_kwargs: [],
    )
    monkeypatch.setattr(consumer, "get_codecompass_agentic_retrieval_service", lambda: service)
    result = consumer.retrieve_architecture_context(
        query="billing subsystem",
        level="subsystem",
        expand=True,
        capability=_capability(),
    )
    assert result["schema"] == SCHEMA_ID
    assert result["plan"]["mode"] in {"hybrid", "graph"}
    assert result["diagnostics"]["architecture_level"] == "subsystem"
    assert result["diagnostics"]["scope"]["revision"] == "rev-abc"


@pytest.mark.parametrize(
    ("query", "mode", "exact_path", "vector_path", "expect_path"),
    [
        ("PaymentService", "exact", "src/payment_service.py", "docs/pay.md", "src/payment_service.py"),
        ("retry policy concept", "vector", "src/payment_service.py", "docs/retry.md", "docs/retry.md"),
        ("how is payment timeout implemented", "hybrid", "src/payment_service.py", "docs/pay.md", "src/payment_service.py"),
    ],
)
def test_e2e_fixture_matrix(query, mode, exact_path, vector_path, expect_path) -> None:
    result = _service(
        exact_search=lambda q, **_kwargs: [
            {"id": "ex", "path": exact_path, "content": "class PaymentService: timeout", "score": 0.6}
        ],
        vector_search=lambda q, **_kwargs: [
            {"id": "ve", "path": vector_path, "content": "semantic retry policy", "score": 0.95}
        ],
        graph_search=lambda q, **_kwargs: [],
        ).retrieve(
            _request(query=query, mode=mode),
            capability=_capability(allowed_paths=["src", "docs"]),
        )
    assert result["status"] in {"ok", "degraded"}
    assert result["evidence"][0]["path"] == expect_path


def test_no_result_fixture() -> None:
    result = _service(
        exact_search=lambda *a, **k: [],
        vector_search=lambda *a, **k: [],
        graph_search=lambda *a, **k: [],
    ).retrieve(_request(query="unknown symbol ZzNoSuchThing", mode="hybrid"))
    assert result["status"] in {"empty", "degraded"}
    assert result["evidence"] == []


def test_index_state_invalidates_changed_manifest() -> None:
    from agent.services.codecompass_agentic_index_state import load_agentic_index_state

    state = load_agentic_index_state(
        {"manifest_hash": "aaa", "model": "local_hash", "dimensions": 8},
        expected={"manifest_hash": "bbb", "model": "local_hash", "dimensions": 8},
    )
    assert state["status"] == "stale"
    assert state["reason"] == REASON_VECTOR_STALE


def test_diagnostics_omit_secrets() -> None:
    result = _service(
        exact_search=lambda *a, **k: [{"id": "e", "path": "src/a.py", "content": "a", "score": 0.4}],
        index_state=lambda: {
            "status": "ready",
            "manifest_hash": "deadbeef",
            "model": "local_hash",
            "authorization": "Bearer secret-token",
        },
    ).retrieve(_request(mode="exact"))
    dumped = __import__("json").dumps(result)
    assert "secret-token" not in dumped
    assert "Bearer" not in dumped
    assert result["diagnostics"]["index"]["manifest_hash"] == "deadbeef"
