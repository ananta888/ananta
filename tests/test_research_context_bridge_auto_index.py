from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.services.research_context_bridge_service import ResearchContextBridgeService


def _make_service(repo_root: Path) -> ResearchContextBridgeService:
    svc = ResearchContextBridgeService()
    svc._repo_root = lambda: repo_root
    return svc


def test_repo_scope_context_returns_directory_listing_when_auto_index_disabled(tmp_path):
    (tmp_path / "models.py").write_text("class Foo: pass\n", encoding="utf-8")
    svc = _make_service(tmp_path)

    with patch.object(svc, "_auto_index_cfg", return_value={"enabled": False}):
        items = svc._repo_scope_context([{"path": "."}], per_item_limit=2000)

    assert len(items) == 1
    assert items[0]["status"] == "directory"
    assert any("models.py" in e for e in items[0]["entries"])


def test_repo_scope_context_triggers_background_indexing_when_enabled(tmp_path):
    (tmp_path / "svc.py").write_text("def run(): pass\n", encoding="utf-8")
    svc = _make_service(tmp_path)

    triggered: list[str] = []

    def _fake_trigger(scope_id, *, profile):
        triggered.append(scope_id)

    with patch.object(svc, "_auto_index_cfg", return_value={"enabled": True, "profile": "default"}), \
         patch.object(svc, "_trigger_path_indexing", side_effect=_fake_trigger), \
         patch("agent.services.research_context_bridge_service.get_repository_registry") as mock_repos:
        mock_repos.return_value.knowledge_index_repo.get_by_scope.return_value = None
        items = svc._repo_scope_context([{"path": "."}], per_item_limit=2000)

    assert len(triggered) == 1
    assert triggered[0] == "."
    assert items[0]["status"] in ("directory", "indexing")


def test_repo_scope_context_returns_indexed_chunks_when_completed(tmp_path):
    svc = _make_service(tmp_path)

    fake_chunk = SimpleNamespace(source="svc.py:5", content="def run(): pass", score=0.9)
    mock_index = SimpleNamespace(status="completed")

    with patch.object(svc, "_auto_index_cfg", return_value={"enabled": True, "profile": "default"}), \
         patch("agent.services.research_context_bridge_service.get_repository_registry") as mock_repos, \
         patch("agent.services.research_context_bridge_service.get_knowledge_index_retrieval_service") as mock_ret:
        mock_repos.return_value.knowledge_index_repo.get_by_scope.return_value = mock_index
        mock_ret.return_value.search.return_value = [fake_chunk]
        # path: "." is the repo root itself
        safe_path = tmp_path
        safe_path.mkdir(exist_ok=True)
        items = svc._repo_scope_context([{"path": ".", "ref": "search query"}], per_item_limit=2000)

    assert items[0]["status"] == "indexed"
    assert len(items[0]["chunks"]) == 1
    assert items[0]["chunks"][0]["source"] == "svc.py:5"


def test_repo_scope_context_does_not_retrigger_running_index(tmp_path):
    svc = _make_service(tmp_path)
    triggered: list[str] = []

    def _fake_trigger(scope_id, *, profile):
        triggered.append(scope_id)

    mock_index = SimpleNamespace(status="running")

    with patch.object(svc, "_auto_index_cfg", return_value={"enabled": True, "profile": "default"}), \
         patch.object(svc, "_trigger_path_indexing", side_effect=_fake_trigger), \
         patch("agent.services.research_context_bridge_service.get_repository_registry") as mock_repos:
        mock_repos.return_value.knowledge_index_repo.get_by_scope.return_value = mock_index
        svc._repo_scope_context([{"path": "."}], per_item_limit=2000)

    assert triggered == []
