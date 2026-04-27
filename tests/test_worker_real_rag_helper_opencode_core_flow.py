from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent.services.ingestion_service import IngestionService
from agent.services.rag_helper_index_service import RagHelperIndexService
from agent.services.task_scoped_execution_service import TaskScopedExecutionService
from worker.adapters.opencode_adapter import OpenCodeAdapter


PROJECT_ROOT = Path(__file__).parent / "fixtures" / "mini_coding_project"


def _upload_and_index_java_artifact(filename: str, *, created_by: str = "tester") -> tuple[dict, dict, list[dict]]:
    source = PROJECT_ROOT / "src" / "main" / "java" / "example" / "security" / filename
    content = source.read_bytes()
    artifact, _version, _collection = IngestionService().upload_artifact(
        filename=filename,
        content=content,
        created_by=created_by,
        media_type="text/x-java-source",
    )
    knowledge_index, run = RagHelperIndexService().index_artifact(
        artifact.id,
        created_by=created_by,
        profile_name="deep_code",
    )

    output_dir = Path(str(run.output_dir))
    records = [
        json.loads(line)
        for line in (output_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return knowledge_index.model_dump(), run.model_dump(), records


def test_real_rag_helper_worker_opencode_core_flow_indexes_java_and_builds_auditable_plan(app, tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/opencode" if name == "opencode" else None)

    indexed = [
        _upload_and_index_java_artifact("SecurityController.java"),
        _upload_and_index_java_artifact("TokenVerifier.java"),
        _upload_and_index_java_artifact("PolicyService.java"),
    ]
    rag_records = []
    for _knowledge_index, run, records in indexed:
        assert run["status"] == "completed"
        rag_records.extend(records)

    rag_context = {
        "index_kind": "real_rag_helper_index_service",
        "profile_name": "deep_code",
        "record_count": len(rag_records),
        "records": rag_records[:12],
    }
    rag_context_text = json.dumps(rag_context, indent=2, sort_keys=True)

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **dict(app.config.get("AGENT_CONFIG") or {}),
            "worker_runtime": {"workspace_root": str(tmp_path)},
        }
        task = {
            "id": "task-real-rag-helper-worker-opencode",
            "title": "Patch Java security secret rotation flow",
            "description": "Use real rag-helper output and Keycloak-style guidance to propose a safe Java patch plan.",
            "task_kind": "coding",
            "required_capabilities": ["coding", "java", "security", "rag_helper"],
            "worker_execution_context": {
                "context": {
                    "context_text": (
                        "Reference profile: ref.java.keycloak\n"
                        "Reference repo: keycloak/keycloak\n"
                        "Boundary: guidance_not_clone; no blind copy.\n\n"
                        f"RAG helper context:\n{rag_context_text}"
                    ),
                    "reference_profile_id": "ref.java.keycloak",
                    "rag_index_kind": "real_rag_helper_index_service",
                },
                "expected_output_schema": {
                    "type": "object",
                    "required": ["reason", "patch_plan", "tests", "safety_notes"],
                },
            },
        }
        prompt, meta = TaskScopedExecutionService()._build_task_propose_prompt(
            tid=task["id"],
            task=task,
            base_prompt="Propose a safe OpenCode coding plan. Do not execute or apply changes.",
            tool_definitions_resolver=lambda allowlist=None: [{"name": "opencode", "allowlist": allowlist or []}],
            research_context=None,
        )

    workspace_dir = Path(meta["workspace"]["workspace_dir"])
    hub_context = (workspace_dir / ".ananta" / "hub-context.md").read_text(encoding="utf-8")
    plan = OpenCodeAdapter(enabled=True).plan(task_id=task["id"], capability_id="coding", prompt=prompt)
    evidence = {
        "task_id": task["id"],
        "rag_record_count": len(rag_records),
        "workspace_dir": str(workspace_dir),
        "hub_context_hash": hashlib.sha256(hub_context.encode("utf-8")).hexdigest(),
        "opencode_plan": plan,
    }

    joined_records = json.dumps(rag_records).lower()
    assert len(rag_records) >= 3
    assert "securitycontroller" in joined_records
    assert "tokenverifier" in joined_records
    assert "policyservice" in joined_records
    assert "real_rag_helper_index_service" in hub_context
    assert "ref.java.keycloak" in hub_context
    assert "keycloak/keycloak" in hub_context
    assert "SecurityController" in hub_context
    assert "TokenVerifier" in hub_context
    assert "PolicyService" in hub_context
    assert plan["schema"] == "command_plan_artifact.v1"
    assert plan["required_approval"] is True
    assert plan["risk_classification"] == "high"
    assert "No direct execution" in " ".join(plan["expected_effects"])
    assert evidence["hub_context_hash"]
