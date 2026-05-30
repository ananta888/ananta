from __future__ import annotations

from client_surfaces.operator_tui.hub_loader import _fetch_audit, _fetch_templates
from client_surfaces.operator_tui.models import PanelState


def test_fetch_templates_uses_local_fallback_when_hub_unavailable(monkeypatch) -> None:
    def _fail(*_args, **_kwargs):
        raise OSError("hub unavailable")

    monkeypatch.setattr("client_surfaces.operator_tui.hub_loader._checked_get", _fail)

    result = _fetch_templates("http://localhost:5000", "token", 1.0)

    assert result.section_id == "templates"
    assert result.state in {PanelState.HEALTHY, PanelState.EMPTY}
    items = list((result.payload or {}).get("items") or [])
    assert items, "expected local template fallback items"
    assert "hub+local" in str(result.message)


def test_fetch_audit_collects_multiple_datasets(monkeypatch) -> None:
    sample = {
        "/api/system/audit-logs?limit=200&offset=0": [{"id": "a1"}],
        "/api/system/audit-logs/summary?limit=1000": {"items": [{"kind": "chat"}]},
        "/api/system/audit-logs/integrity?limit=500": {"ok": True},
        "/api/system/stats": {"uptime_seconds": 10},
        "/api/system/stats/history?limit=120&offset=0": {"items": [1, 2, 3]},
        "/debug/backend-observability?lookback_seconds=3600&trace_limit=200": {"items": []},
        "/debug/llm-requests?limit=120": {"items": [{"id": "llm-1"}]},
        "/tasks/timeline?limit=50": {"items": [{"task_id": "t1"}]},
        "/tasks?limit=50": [{"id": "t1"}],
    }

    def _fake_get(_base, path, _token, _timeout):
        if path in sample:
            return sample[path]
        raise OSError("missing path")

    monkeypatch.setattr("client_surfaces.operator_tui.hub_loader._checked_get", _fake_get)

    result = _fetch_audit("http://localhost:5000", "token", 1.0)

    assert result.section_id == "audit"
    assert result.state is PanelState.HEALTHY
    payload = dict(result.payload or {})
    items = list(payload.get("items") or [])
    datasets = dict(payload.get("datasets") or {})
    assert len(items) == 15
    assert "audit.logs.recent" in datasets
    assert "runtime.stats.snapshot" in datasets
    assert "llm.requests.recent" in datasets
    assert "audit.cleanup.all" in datasets


def test_fetch_audit_adds_chat_prompt_trace_items(monkeypatch) -> None:
    sample = {
        "/api/system/audit-logs?limit=200&offset=0": [],
        "/api/system/audit-logs/summary?limit=1000": {"items": []},
        "/api/system/audit-logs/integrity?limit=500": {"ok": True},
        "/api/system/stats": {"uptime_seconds": 10},
        "/api/system/stats/history?limit=120&offset=0": {"items": []},
        "/debug/backend-observability?lookback_seconds=3600&trace_limit=200": {"items": []},
        "/debug/llm-requests?limit=120": {
            "traces": [
                {
                    "trace_id": "trace-1",
                    "request_kind": "chat.ask",
                    "source_component": "operator_tui.chat",
                    "model": "local-model",
                    "created_at": "2026-05-30T10:00:00Z",
                    "prompt_preview_redacted": "preview",
                }
            ]
        },
        "/debug/llm-requests/trace-1": {
            "trace_id": "trace-1",
            "final_prompt_redacted": "SYSTEM: context\nUSER: question",
            "messages_redacted": [{"role": "user", "content": "question"}],
        },
        "/tasks/timeline?limit=50": {"items": []},
        "/tasks?limit=50": [],
    }

    def _fake_get(_base, path, _token, _timeout):
        if path in sample:
            return sample[path]
        raise OSError("missing path")

    monkeypatch.setattr("client_surfaces.operator_tui.hub_loader._checked_get", _fake_get)

    result = _fetch_audit("http://localhost:5000", "token", 2.0)

    payload = dict(result.payload or {})
    items = list(payload.get("items") or [])
    datasets = dict(payload.get("datasets") or {})
    chat_rows = [row for row in items if str(row.get("id") or "").startswith("llm.requests.chat_prompt.")]
    assert chat_rows, "expected per-chat prompt rows in audit payload"
    dataset_id = str(chat_rows[0].get("dataset_id") or "")
    detail_payload = dict(datasets.get(dataset_id) or {})
    assert detail_payload.get("trace_id") == "trace-1"
    assert "SYSTEM: context" in str(detail_payload.get("final_prompt_redacted") or "")
