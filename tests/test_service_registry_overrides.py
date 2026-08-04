from __future__ import annotations

from dataclasses import dataclass

import pytest
from flask import Flask

from agent.services import service_registry


@dataclass(frozen=True)
class _KnowledgeServices:
    knowledge_index_job_service: object


@dataclass(frozen=True)
class _CoreServices:
    knowledge: _KnowledgeServices


def test_override_is_applied_immediately_and_after_registry_rebuild(
    monkeypatch,
) -> None:
    app = Flask(__name__)
    initial_default = object()
    override = object()
    app.extensions["core_services"] = _CoreServices(
        knowledge=_KnowledgeServices(initial_default)
    )

    service_registry.register_core_service_override(
        app,
        service_field_name="knowledge_index_job_service",
        service=override,
    )

    immediate = app.extensions["core_services"]
    assert immediate.knowledge.knowledge_index_job_service is override

    rebuilt_default = object()
    rebuilt = _CoreServices(
        knowledge=_KnowledgeServices(rebuilt_default)
    )
    monkeypatch.setattr(
        service_registry,
        "build_core_service_registry",
        lambda app: rebuilt,
    )

    reinitialized = service_registry.initialize_core_services(app)

    assert reinitialized is not immediate
    assert reinitialized.knowledge.knowledge_index_job_service is override


def test_override_registered_before_registry_is_applied_on_initialization(
    monkeypatch,
) -> None:
    app = Flask(__name__)
    override = object()
    rebuilt = _CoreServices(knowledge=_KnowledgeServices(object()))
    monkeypatch.setattr(
        service_registry,
        "build_core_service_registry",
        lambda app: rebuilt,
    )

    service_registry.register_core_service_override(
        app,
        service_field_name="knowledge_index_job_service",
        service=override,
    )

    assert "core_services" not in app.extensions
    initialized = service_registry.initialize_core_services(app)
    assert initialized.knowledge.knowledge_index_job_service is override


def test_unknown_override_field_is_rejected_without_app_mutation() -> None:
    app = Flask(__name__)
    registry = _CoreServices(knowledge=_KnowledgeServices(object()))
    app.extensions["core_services"] = registry

    with pytest.raises(
        ValueError,
        match="^core_service_override_field_unknown$",
    ):
        service_registry.register_core_service_override(
            app,
            service_field_name="missing_service",
            service=object(),
        )

    assert app.extensions["core_services"] is registry
    assert "core_service_overrides" not in app.extensions
