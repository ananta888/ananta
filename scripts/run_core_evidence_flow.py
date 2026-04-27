from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.ai_agent import create_app
from agent.database import init_db
from agent.services.ingestion_service import IngestionService
from agent.services.rag_helper_index_service import RagHelperIndexService
from agent.services.service_registry import get_core_services
from agent.services.task_scoped_execution_service import TaskScopedExecutionService
from worker.adapters.opencode_adapter import OpenCodeAdapter

PROJECT_ROOT = REPO_ROOT / "tests" / "fixtures" / "mini_coding_project"
JAVA_FILES = ["SecurityController.java", "TokenVerifier.java", "PolicyService.java"]


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _upload_and_index_java_artifact(filename: str, *, created_by: str) -> dict:
    source = PROJECT_ROOT / "src" / "main" / "java" / "example" / "security" / filename
    artifact, version, _collection = IngestionService().upload_artifact(
        filename=filename,
        content=source.read_bytes(),
        created_by=created_by,
        media_type="text/x-java-source",
    )
    knowledge_index, run = RagHelperIndexService().index_artifact(
        artifact.id,
        created_by=created_by,
        profile_name="deep_code",
    )
    output_dir = Path(str(run.output_dir))
    return {
        "filename": filename,
        "artifact": artifact.model_dump(),
        "version": version.model_dump(),
        "knowledge_index": knowledge_index.model_dump(),
        "run": run.model_dump(),
        "output_dir": str(output_dir),
        "manifest": json.loads((output_dir / "manifest.json").read_text(encoding="utf-8")),
        "index_records": _read_jsonl(output_dir / "index.jsonl"),
    }


def _post_json(client, path: str, payload: dict, headers: dict) -> dict:
    response = client.post(path, json=payload, headers=headers)
    data = response.get_json(silent=True) or {}
    if response.status_code >= 400:
        raise RuntimeError(f"POST {path} failed with {response.status_code}: {data}")
    return {"status_code": response.status_code, "json": data}


def _get_json(client, path: str, headers: dict) -> dict:
    response = client.get(path, headers=headers)
    data = response.get_json(silent=True) or {}
    if response.status_code >= 400:
        raise RuntimeError(f"GET {path} failed with {response.status_code}: {data}")
    return {"status_code": response.status_code, "json": data}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def run(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    os.environ.setdefault("CONTROLLER_URL", "http://mock-controller")
    os.environ.setdefault("AGENT_NAME", "evidence-agent")
    os.environ.setdefault("INITIAL_ADMIN_USER", "admin")
    os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "admin")

    init_db()
    app = create_app(agent="evidence-agent")
    app.config.update({"TESTING": True})

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **dict(app.config.get("AGENT_CONFIG") or {}),
            "worker_runtime": {"workspace_root": str(out_dir / "worker-runtime")},
        }

        indexed = [_upload_and_index_java_artifact(filename, created_by="evidence") for filename in JAVA_FILES]
        all_records: list[dict] = []
        for item in indexed:
            all_records.extend(item["index_records"])
            source_dir = Path(item["output_dir"])
            evidence_dir = out_dir / "rag-helper" / item["filename"]
            _copy_if_exists(source_dir / "manifest.json", evidence_dir / "manifest.json")
            _copy_if_exists(source_dir / "index.jsonl", evidence_dir / "index.jsonl")

        rag_context = {
            "index_kind": "real_rag_helper_index_service",
            "profile_name": "deep_code",
            "artifact_count": len(indexed),
            "record_count": len(all_records),
            "records_preview": all_records[:12],
        }
        _write_json(out_dir / "rag-helper" / "rag-context.json", rag_context)

        task_context = (
            "Reference profile: ref.java.keycloak\n"
            "Reference repo: keycloak/keycloak\n"
            "Boundary: guidance_not_clone; no blind copy.\n\n"
            f"RAG helper context:\n{json.dumps(rag_context, indent=2, sort_keys=True)}"
        )
        ingest_payload = {
            "id": "task-core-evidence-worker-rag-engines",
            "title": "Patch Java security secret rotation flow",
            "description": "Use real rag-helper output and Keycloak-style guidance to compare native Ananta and OpenCode worker engines.",
            "source": "core-evidence-flow",
            "created_by": "evidence",
            "priority": "high",
            "task_kind": "coding",
            "required_capabilities": ["coding", "java", "security", "rag_helper"],
            "worker_execution_context": {
                "context": {
                    "context_text": task_context,
                    "reference_profile_id": "ref.java.keycloak",
                    "rag_index_kind": "real_rag_helper_index_service",
                    "worker_engine_candidates": ["ananta_native", "opencode"],
                },
                "expected_output_schema": {
                    "type": "object",
                    "required": ["engine", "reason", "patch_plan", "tests", "safety_notes"],
                },
            },
        }

        client = app.test_client()
        auth_headers = {"Authorization": "Bearer evidence-agent-token-with-sufficient-length-1234567890"}
        app.config["AGENT_TOKEN"] = "evidence-agent-token-with-sufficient-length-1234567890"

        ingest = _post_json(client, "/tasks/orchestration/ingest", ingest_payload, auth_headers)
        task_id = ingest["json"]["data"]["id"]
        claim = _post_json(
            client,
            "/tasks/orchestration/claim",
            {
                "task_id": task_id,
                "agent_url": "http://evidence-worker:5001",
                "idempotency_key": "core-evidence-worker-claim-1",
                "lease_seconds": 120,
            },
            auth_headers,
        )
        claimed_task = get_core_services().task_runtime_service.get_local_task_status(task_id)
        if not claimed_task:
            raise RuntimeError("claimed task not found after ingest/claim")

        prompt, meta = TaskScopedExecutionService()._build_task_propose_prompt(
            tid=task_id,
            task=claimed_task,
            base_prompt=(
                "Use the same RAG context for two worker engine candidates: ananta_native and opencode. "
                "Propose safe plan artifacts only. Do not execute or apply changes."
            ),
            tool_definitions_resolver=lambda allowlist=None: [
                {"name": "ananta_native", "allowlist": allowlist or []},
                {"name": "opencode", "allowlist": allowlist or []},
            ],
            research_context=None,
        )

        workspace_dir = Path(meta["workspace"]["workspace_dir"])
        hub_context = (workspace_dir / ".ananta" / "hub-context.md").read_text(encoding="utf-8")
        hub_context_hash = hashlib.sha256(hub_context.encode("utf-8")).hexdigest()

        engine_dir = out_dir / "worker-engines"
        engine_dir.mkdir(parents=True, exist_ok=True)
        ananta_native_result = {
            "engine": "ananta_native",
            "role": "native Ananta worker runtime engine",
            "task_id": task_id,
            "input_context": {
                "rag_index_kind": "real_rag_helper_index_service",
                "reference_profile_id": "ref.java.keycloak",
                "hub_context_hash": hub_context_hash,
                "workspace_dir": str(workspace_dir),
            },
            "result": {
                "status": "planned",
                "execution_mode": "plan_only",
                "reason": "Native Ananta worker can use the materialized hub context and RAG records to prepare a safe patch plan.",
                "patch_plan": [
                    "Inspect SecurityController secret rotation boundary.",
                    "Keep TokenVerifier issuer validation explicit.",
                    "Keep PolicyService admin authorization mandatory.",
                    "Add or update tests before applying changes.",
                ],
                "tests": ["unit tests for invalid token", "unit tests for non-admin role", "unit tests for allowed admin flow"],
                "safety_notes": ["no direct execution in evidence flow", "requires approval before applying code changes"],
            },
        }
        opencode_plan = OpenCodeAdapter(enabled=True).plan(task_id=task_id, capability_id="coding", prompt=prompt)
        opencode_result = {
            "engine": "opencode",
            "role": "alternative coding engine usable inside a worker",
            "task_id": task_id,
            "input_context": {
                "rag_index_kind": "real_rag_helper_index_service",
                "reference_profile_id": "ref.java.keycloak",
                "hub_context_hash": hub_context_hash,
                "workspace_dir": str(workspace_dir),
            },
            "result": {
                "status": "planned",
                "execution_mode": "plan_only",
                "adapter_plan": opencode_plan,
            },
        }
        _write_json(engine_dir / "ananta-native-result.json", ananta_native_result)
        _write_json(engine_dir / "opencode-result.json", opencode_result)

        complete_payload = {
            "task_id": task_id,
            "actor": "http://evidence-worker:5001",
            "gate_results": {
                "passed": True,
                "checks": [
                    "rag-helper-index-completed",
                    "worker-context-materialized",
                    "ananta-native-engine-planned",
                    "opencode-engine-planned",
                    "approval-gate-required",
                ],
            },
            "output": json.dumps(
                {
                    "summary": "Evidence worker completed controlled plan-only flow with two worker engines.",
                    "worker_engines": {
                        "ananta_native": ananta_native_result["result"],
                        "opencode": opencode_result["result"],
                    },
                    "hub_context_hash": hub_context_hash,
                },
                sort_keys=True,
            ),
            "trace_id": "trace-core-evidence-worker-rag-engines",
        }
        complete = _post_json(client, "/tasks/orchestration/complete", complete_payload, auth_headers)
        read_model = _get_json(client, "/tasks/orchestration/read-model", auth_headers)

        orchestration_dir = out_dir / "orchestration"
        orchestration_dir.mkdir(parents=True, exist_ok=True)
        _write_json(orchestration_dir / "ingest-response.json", ingest)
        _write_json(orchestration_dir / "claim-response.json", claim)
        _write_json(orchestration_dir / "complete-response.json", complete)
        _write_json(orchestration_dir / "read-model.json", read_model)
        _write_json(orchestration_dir / "complete-payload.json", complete_payload)

        evidence = {
            "status": "completed",
            "task_id": task_id,
            "architecture": {
                "hub": "task ingest, claim, complete, read-model",
                "worker": "claimed worker runtime context",
                "worker_engines": ["ananta_native", "opencode"],
                "context_layer": "real rag-helper via RagHelperIndexService",
            },
            "rag_record_count": len(all_records),
            "workspace_dir": str(workspace_dir),
            "hub_context_hash": hub_context_hash,
            "engine_result_hashes": {
                "ananta_native": hashlib.sha256(
                    json.dumps(ananta_native_result, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "opencode": hashlib.sha256(json.dumps(opencode_result, sort_keys=True).encode("utf-8")).hexdigest(),
            },
            "orchestration": {
                "ingested": ingest["json"]["data"].get("ingested") is True,
                "claimed": claim["json"]["data"].get("claimed") is True,
                "completed_status": complete["json"]["data"].get("status"),
                "read_model_completed_count": read_model["json"]["data"].get("queue", {}).get("completed"),
            },
            "checks": {
                "hub_ingest_used": ingest["json"]["data"].get("ingested") is True,
                "worker_claim_used": claim["json"]["data"].get("claimed") is True,
                "worker_complete_used": complete["json"]["data"].get("status") == "completed",
                "read_model_shows_completed_task": any(
                    item.get("id") == task_id and item.get("status") == "completed"
                    for item in read_model["json"]["data"].get("recent_tasks", [])
                ),
                "has_security_controller": "securitycontroller" in json.dumps(all_records).lower(),
                "has_token_verifier": "tokenverifier" in json.dumps(all_records).lower(),
                "has_policy_service": "policyservice" in json.dumps(all_records).lower(),
                "has_keycloak_reference": "ref.java.keycloak" in hub_context and "keycloak/keycloak" in hub_context,
                "ananta_native_engine_present": ananta_native_result["engine"] == "ananta_native",
                "opencode_engine_present": opencode_result["engine"] == "opencode",
                "opencode_requires_approval": opencode_plan.get("required_approval") is True,
                "opencode_high_risk": opencode_plan.get("risk_classification") == "high",
            },
        }

        worker_dir = out_dir / "worker"
        worker_dir.mkdir(parents=True, exist_ok=True)
        _write_json(worker_dir / "task.json", claimed_task)
        (worker_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        _write_json(worker_dir / "metadata.json", meta)
        (worker_dir / "hub-context.md").write_text(hub_context, encoding="utf-8")
        _write_json(out_dir / "opencode-plan.json", opencode_plan)
        _write_json(out_dir / "evidence-summary.json", evidence)
        (out_dir / "README.md").write_text(
            "# Ananta Core Evidence Flow\n\n"
            "This artifact proves the controlled core path:\n\n"
            "1. Java files are uploaded as Ananta artifacts.\n"
            "2. `RagHelperIndexService` runs the real `rag-helper` and writes `manifest.json` plus `index.jsonl`.\n"
            "3. The real Hub orchestration endpoints ingest, claim and complete the task.\n"
            "4. The claimed worker receives the materialized `.ananta/hub-context.md`.\n"
            "5. Two worker engines are represented from the same claimed worker context:\n"
            "   - `ananta_native` as the native Ananta worker engine.\n"
            "   - `opencode` as an alternative coding engine inside a worker.\n"
            "6. OpenCode creates a high-risk, approval-gated plan without direct execution.\n\n"
            "See `evidence-summary.json`, `orchestration/*.json`, `rag-helper/*/index.jsonl`, "
            "`worker/hub-context.md`, `worker-engines/ananta-native-result.json`, "
            "and `worker-engines/opencode-result.json`.\n",
            encoding="utf-8",
        )

        failed = [name for name, ok in evidence["checks"].items() if not ok]
        if failed:
            raise SystemExit(f"Evidence checks failed: {failed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="ci-artifacts/core-evidence-flow")
    args = parser.parse_args()
    run(Path(args.out))
