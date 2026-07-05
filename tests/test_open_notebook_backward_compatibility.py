import pytest

from agent.config import settings
from agent.services.retrieval_source_contract import normalize_requested_source_types
from tests.test_retrieval_service_open_notebook_source_type import _service


def test_open_notebook_is_disabled_by_default():
    assert type(settings).model_fields["rag_source_open_notebook_enabled"].default is False


def test_repo_and_artifact_requests_do_not_call_open_notebook(monkeypatch):
    service, knowledge = _service(monkeypatch, open_notebook_enabled=True)
    service.retrieve_context("repo", source_types=["repo"])
    assert {"open_notebook"} not in knowledge.scope_calls
    knowledge.scope_calls.clear()
    service.retrieve_context("artifact", source_types=["artifact"])
    assert {"open_notebook"} not in knowledge.scope_calls


def test_unknown_source_type_remains_rejected():
    with pytest.raises(ValueError, match="invalid_source_type"):
        normalize_requested_source_types(["unknown"])
