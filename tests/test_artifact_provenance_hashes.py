from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agent.repository import artifact_version_repo
from agent.services.task_execution_tracking_service import get_task_execution_tracking_service
from agent.services.worker_workspace_service import WorkerWorkspaceContext, WorkerWorkspaceService

ROOT = Path(__file__).resolve().parents[1]
TRACE_BUNDLE_SCHEMA_PATH = ROOT / "schemas" / "trace" / "trace_bundle.v1.json"


def _trace_bundle_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(TRACE_BUNDLE_SCHEMA_PATH.read_text(encoding="utf-8")))


def test_workspace_artifact_refs_include_task_execution_reference_and_stable_digest(app, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "patch.diff").write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")

    service = WorkerWorkspaceService()
    with app.app_context():
        refs = service.sync_changed_files_to_artifacts(
            task_id="T-ART-REF-1",
            task={"current_worker_job_id": "job-1", "assigned_agent_url": "http://worker-1:5001"},
            workspace_dir=workspace,
            changed_rel_paths=["patch.diff"],
            sync_cfg={
                "enabled": True,
                "sync_to_hub": True,
                "collection_name": "task-execution-results",
                "max_changed_files": 5,
                "max_file_size_bytes": 1024 * 1024,
            },
        )

    assert len(refs) == 1
    ref = refs[0]
    assert ref["kind"] == "workspace_file"
    assert ref["task_id"] == "T-ART-REF-1"
    assert ref["worker_job_id"] == "job-1"
    assert ref["artifact_id"]
    assert ref["artifact_version_id"]
    assert ref["content_hash"] and len(ref["content_hash"]) == 64
    assert (ref.get("provenance_summary") or {}).get("artifact_type") == "workspace_file"


def test_research_artifact_refs_include_content_hash_and_provenance_summary(app) -> None:
    with app.app_context():
        ref = get_task_execution_tracking_service().persist_research_artifact(
            tid="T-ART-REF-2",
            task={"current_worker_job_id": "job-research-1", "assigned_agent_url": "http://worker-1:5001"},
            research_artifact={
                "kind": "research_report",
                "summary": "Summary",
                "report_markdown": "# Report\n\nBody\n",
                "sources": [{"title": "Source", "url": "https://example.com"}],
                "citations": [{"source": "https://example.com"}],
                "trace": {"trace_bundle_id": "tb-1"},
            },
        )

    assert ref is not None
    assert ref["kind"] == "research_report"
    assert ref["task_id"] == "T-ART-REF-2"
    assert ref["worker_job_id"] == "job-research-1"
    assert ref["content_hash"] and len(ref["content_hash"]) == 64
    assert (ref.get("provenance_summary") or {}).get("source_count") == 1
    assert (ref.get("provenance_summary") or {}).get("trace_bundle_ref") == "tb-1"

    version = artifact_version_repo.get_by_id(ref["artifact_version_id"])
    assert version is not None
    assert version.sha256 == ref["content_hash"]


def test_workspace_context_bundle_keeps_rag_source_refs_when_context_influences_output(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "ws"
    artifacts_dir = workspace_dir / "artifacts"
    rag_helper_dir = workspace_dir / "rag_helper"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    rag_helper_dir.mkdir(parents=True, exist_ok=True)

    context = WorkerWorkspaceContext(
        workspace_dir=workspace_dir,
        artifacts_dir=artifacts_dir,
        rag_helper_dir=rag_helper_dir,
        artifact_sync={"enabled": True, "sync_to_hub": True, "collection_name": "task-execution-results"},
    )
    research_context = {
        "artifact_ids": ["artifact-123"],
        "knowledge_collection_ids": ["kc-1"],
        "repo_scope_refs": [{"path": "src/main.py", "ref": "main"}],
        "prompt_section": "Repo-Kontext:\n- Repo-Scope src/main.py: file",
    }

    manifest = WorkerWorkspaceService().prepare_opencode_context_files(
        task={"id": "T-CONTEXT-1", "title": "Task title", "description": "Task description"},
        workspace_context=context,
        base_prompt="Implement change",
        system_prompt=None,
        context_text=None,
        expected_output_schema=None,
        tool_definitions=None,
        research_context=research_context,
    )

    research_context_path = workspace_dir / str(manifest["research_context_json_path"])
    research_json = json.loads(research_context_path.read_text(encoding="utf-8"))
    context_index = (workspace_dir / str(manifest["context_index_path"])).read_text(encoding="utf-8")

    assert research_json["repo_scope_refs"] == [{"path": "src/main.py", "ref": "main"}]
    assert str(manifest["research_context_json_path"]) in context_index
    assert str(manifest["research_context_prompt_path"]) in context_index


def test_artifact_metadata_can_be_linked_to_trace_bundle_schema() -> None:
    payload = {
        "schema": "trace_bundle.v1",
        "trace_bundle_id": "tb-1",
        "generated_at": "2026-04-25T16:38:54+02:00",
        "goal_id": "goal-1",
        "plan_id": "plan-1",
        "task_ids": ["task-1"],
        "model": {"provider": "openai", "model_id": "gpt-5.3-codex"},
        "prompt_template": {"template_id": "tpl-1", "version": "v1"},
        "context_hash": "a" * 64,
        "rag_refs": [{"source_ref": "artifact:doc-1#chunk-1", "retrieval_reason": "query_match", "chunk_ids": ["c-1"]}],
        "policy_decisions": [{"decision_id": "pol-1", "outcome": "allow", "reason_code": "allowed"}],
        "approval_decisions": [
            {
                "approval_id": "apr-1",
                "action_ref": "task-1:execute",
                "state": "approved",
                "context_hash": "a" * 64,
            }
        ],
        "artifact_refs": [
            {
                "artifact_id": "artifact-1",
                "kind": "workspace_file",
                "task_id": "task-1",
                "execution_ref": "job-1",
                "content_hash": "b" * 64,
                "provenance_summary": {"artifact_type": "workspace_file", "workspace_relative_path": "src/main.py"},
            }
        ],
        "provenance": {
            "generator_component": "release_gate",
            "security_profile": "oss_core",
            "contains_full_context": False,
            "notes": ["compact bundle for OSS replay/debug"],
        },
    }

    assert list(_trace_bundle_validator().iter_errors(payload)) == []

