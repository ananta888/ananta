from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
from sqlmodel import Session, create_engine

from agent.config import Settings, settings
from agent.db_models.visual_process import VisualProcessGraphDB
from agent.services.visual_process_context_service import VisualProcessContextService
from agent.services.visual_process_definition_service import VisualProcessDefinitionService
from agent.visual_process.models import VisualProcessGraph

ROOT = Path(__file__).resolve().parents[3]
PRE_DEFINITION_REVISION = "z4a5b6c7d8e9"
DEFINITION_REVISION = "a5b6c7d8e9f0"
ASSISTANT_REVISION = "b6c7d8e9f0a1"
FEATURE_FIELDS = (
    "visual_process_registry_inspector_enabled",
    "visual_process_hover_help_enabled",
    "visual_process_assistant_chat_enabled",
    "visual_process_ai_patches_enabled",
    "visual_process_ai_patch_auto_approval_enabled",
)
ARCHITECTURE_DOC = ROOT / "docs/architecture/visual-process-assistant.md"


def _legacy_graph() -> VisualProcessGraph:
    return VisualProcessGraph.model_validate(
        {
            "id": "legacy-rollback-graph",
            "name": "Legacy rollback graph",
            "version": "0.7",
            "graph_schema_version": "legacy-1",
            "node_registry_version": "legacy-registry",
            "legacy_topology_hint": {"layout": "kept"},
            "metadata": {"legacy_owner_hint": "retained"},
            "steps": [
                {
                    "id": "legacy-step",
                    "label": "Legacy step",
                    "kind": "legacy_custom_kind",
                    "legacy_field": {"unknown": "retained"},
                    "run_state": "running",
                }
            ],
            "runtime_overlay": {
                "schema": "ananta.visual_process.runtime_overlay.v1",
                "cursor": "legacy-step",
            },
        }
    )


def _alembic(database: Path, *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": f"sqlite:///{database}",
            "ANANTA_DATA_DIR": str(database.parent / "data"),
        },
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_all_assistant_feature_and_auto_approval_flags_default_off_and_fail_closed(
    client,
    admin_auth_header,
    monkeypatch,
) -> None:
    assert all(Settings.model_fields[field].default is False for field in FEATURE_FIELDS)
    for field in FEATURE_FIELDS:
        monkeypatch.setattr(settings, field, False)

    capabilities = client.get(
        "/api/visual-process/assistant/v1/capabilities",
        headers=admin_auth_header,
    )
    assert capabilities.status_code == 200
    assert {
        "registry_inspector": False,
        "hover_help": False,
        "assistant_chat": False,
        "ai_patches": False,
        "patch_auto_approval_enabled": False,
    }.items() <= capabilities.get_json().items()

    registry = client.get(
        "/api/visual-process/v1/node-definitions",
        headers=admin_auth_header,
    )
    assert registry.status_code == 404
    assert registry.get_json()["error_code"] == "visual_process_registry_inspector_disabled"

    chat = client.get(
        "/api/visual-process/assistant/v1/contexts/unavailable",
        headers=admin_auth_header,
    )
    assert chat.status_code == 404
    assert chat.get_json()["error_code"] == "assistant_feature_disabled"


def test_legacy_definition_and_runtime_overlay_survive_create_edit_reload() -> None:
    database = create_engine("sqlite://")
    VisualProcessGraphDB.__table__.create(database)
    service = VisualProcessDefinitionService()

    with Session(database) as db:
        created = service.create(db, _legacy_graph(), now=1.0)
        db.commit()
        row = db.get(VisualProcessGraphDB, created.graph.id)
        assert row is not None
        loaded = service.load(row)

        assert loaded.graph.version == "0.7"
        assert loaded.graph.steps[0].kind == "legacy_custom_kind"
        assert loaded.graph.steps[0].model_extra == {"legacy_field": {"unknown": "retained"}}
        assert loaded.graph.model_extra == {"legacy_topology_hint": {"layout": "kept"}}
        assert loaded.graph.runtime_overlay == {
            "schema": "ananta.visual_process.runtime_overlay.v1",
            "cursor": "legacy-step",
            "step_states": {"legacy-step": {"step_id": "legacy-step", "status": "running"}},
        }

        changed = loaded.graph.model_copy(update={"description": "edited while rollout flags remain disabled"})
        replaced = service.replace(
            db,
            row,
            changed,
            expected_revision=loaded.definition_revision,
            expected_hash=loaded.base_graph_hash,
            require_precondition=True,
            now=2.0,
        )
        db.commit()
        db.expire_all()
        reloaded_row = db.get(VisualProcessGraphDB, created.graph.id)
        assert reloaded_row is not None
        reloaded = service.load(reloaded_row)

    assert replaced.changed is True
    assert reloaded.definition_revision == 2
    assert reloaded.graph.description == "edited while rollout flags remain disabled"
    assert reloaded.graph.steps[0].model_extra == {"legacy_field": {"unknown": "retained"}}
    assert reloaded.graph.model_extra == {"legacy_topology_hint": {"layout": "kept"}}
    assert reloaded.graph.runtime_overlay == loaded.graph.runtime_overlay


def test_read_only_context_drops_mutation_capabilities_fail_closed() -> None:
    graph = _legacy_graph()

    context = VisualProcessContextService().build_context(
        graph=graph,
        location={"target_kind": "canvas", "graph_id": graph.id},
        editor_mode="read_only",
        repository_revision="legacy-revision",
        codecompass_manifest_hash="legacy-manifest",
        source_allowlist_version="legacy-allowlist",
        runtime_overlay=graph.runtime_overlay,
        allowed_mutations={"update_step_field", "add_step"},
    )

    assert context.editor_mode == "read_only"
    assert context.allowed_mutations == []
    assert context.runtime_overlay == graph.runtime_overlay


def test_assistant_schema_upgrade_downgrade_and_reupgrade(tmp_path: Path) -> None:
    database = tmp_path / "visual-process-assistant-migration.db"

    _alembic(database, "upgrade", PRE_DEFINITION_REVISION)
    legacy_engine = sa.create_engine(f"sqlite:///{database}")
    with legacy_engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE visual_process_graphs ("
            "id VARCHAR NOT NULL PRIMARY KEY, "
            "name VARCHAR NOT NULL, "
            "description VARCHAR NOT NULL DEFAULT '', "
            "tags VARCHAR NOT NULL DEFAULT '', "
            "graph_json VARCHAR NOT NULL DEFAULT '', "
            "created_at FLOAT NOT NULL DEFAULT 0, "
            "updated_at FLOAT NOT NULL DEFAULT 0"
            ")"
        )
    _alembic(database, "upgrade", DEFINITION_REVISION)
    inspector = sa.inspect(sa.create_engine(f"sqlite:///{database}"))
    definition_columns = {item["name"] for item in inspector.get_columns("visual_process_graphs")}
    assert {
        "definition_revision",
        "base_graph_hash",
        "graph_schema_version",
        "node_registry_version",
    } <= definition_columns

    _alembic(database, "upgrade", ASSISTANT_REVISION)
    inspector = sa.inspect(sa.create_engine(f"sqlite:///{database}"))
    assistant_tables = {
        "visual_process_assistant_contexts",
        "visual_process_assistant_conversations",
        "visual_process_assistant_requests",
        "visual_process_assistant_rate_limits",
        "visual_process_patch_audits",
    }
    assert assistant_tables <= set(inspector.get_table_names())

    _alembic(database, "downgrade", DEFINITION_REVISION)
    inspector = sa.inspect(sa.create_engine(f"sqlite:///{database}"))
    assert assistant_tables.isdisjoint(inspector.get_table_names())
    assert "visual_process_graphs" in inspector.get_table_names()

    _alembic(database, "upgrade", ASSISTANT_REVISION)
    inspector = sa.inspect(sa.create_engine(f"sqlite:///{database}"))
    assert assistant_tables <= set(inspector.get_table_names())


def test_architecture_runbook_covers_required_rollout_and_operations_boundaries() -> None:
    documentation = ARCHITECTURE_DOC.read_text(encoding="utf-8").lower()

    for required_section in (
        "hub bleibt control plane",
        "registry und editor-vertrag",
        "versionen und graphzustand",
        "deterministischer editorcontext",
        "evidence-policy",
        "betriebsbudgets",
        "patch-governance",
        "diagnose",
        "rollback",
        "grounded_source_authority_positive",
    ):
        assert required_section in documentation
