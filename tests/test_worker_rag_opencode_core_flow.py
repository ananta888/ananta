from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from agent.services.task_scoped_execution_service import TaskScopedExecutionService
from worker.adapters.opencode_adapter import OpenCodeAdapter


PROJECT_ROOT = Path(__file__).parent / "fixtures" / "mini_coding_project"
REFERENCE_ROOT = Path(__file__).parent / "fixtures" / "java_security_mini"


@dataclass(frozen=True)
class MiniRagChunk:
    source: str
    symbol: str
    text: str


def _mini_rag_index(*roots: Path) -> list[MiniRagChunk]:
    chunks: list[MiniRagChunk] = []
    for root in roots:
        for source in sorted(root.rglob("*.java")):
            chunks.append(
                MiniRagChunk(
                    source=str(source.relative_to(root)),
                    symbol=source.stem,
                    text=source.read_text(encoding="utf-8")[:1600],
                )
            )
    return chunks


def _mini_rag_search(chunks: list[MiniRagChunk], query: str, *, top_k: int = 5) -> list[MiniRagChunk]:
    tokens = [token.lower() for token in query.replace("/", " ").replace("_", " ").replace("-", " ").split()]

    def score(chunk: MiniRagChunk) -> int:
        haystack = f"{chunk.source}\n{chunk.symbol}\n{chunk.text}".lower()
        return sum(1 for token in tokens if token and token in haystack)

    return sorted((chunk for chunk in chunks if score(chunk) > 0), key=lambda chunk: (-score(chunk), chunk.source))[:top_k]


def _build_rag_context() -> dict:
    chunks = _mini_rag_index(PROJECT_ROOT, REFERENCE_ROOT)
    results = _mini_rag_search(
        chunks,
        "Java token verifier policy admin secret rotation keycloak reference",
        top_k=6,
    )
    return {
        "index_kind": "mini_rag_fixture_index",
        "query": "Java token verifier policy admin secret rotation keycloak reference",
        "chunks": [chunk.__dict__ for chunk in results],
        "chunk_count": len(results),
    }


def test_core_worker_rag_opencode_flow_builds_auditable_plan(app, tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/opencode" if name == "opencode" else None)
    rag_context = _build_rag_context()
    rag_context_text = json.dumps(rag_context, indent=2, sort_keys=True)

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **dict(app.config.get("AGENT_CONFIG") or {}),
            "worker_runtime": {"workspace_root": str(tmp_path)},
        }
        task = {
            "id": "task-core-worker-rag-opencode",
            "title": "Patch Java security secret rotation flow",
            "description": "Use RAG context and Keycloak-style reference guidance to propose a safe Java patch plan.",
            "task_kind": "coding",
            "required_capabilities": ["coding", "java", "security", "rag"],
            "worker_execution_context": {
                "context": {
                    "context_text": (
                        "Reference profile: ref.java.keycloak\n"
                        "Reference repo: keycloak/keycloak\n"
                        "Boundary: guidance_not_clone; no blind copy.\n\n"
                        f"RAG context:\n{rag_context_text}"
                    ),
                    "reference_profile_id": "ref.java.keycloak",
                    "rag_index_kind": "mini_rag_fixture_index",
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
    adapter = OpenCodeAdapter(enabled=True)
    plan = adapter.plan(task_id=task["id"], capability_id="coding", prompt=prompt)
    evidence = {
        "task_id": task["id"],
        "rag_chunk_count": rag_context["chunk_count"],
        "rag_symbols": [chunk["symbol"] for chunk in rag_context["chunks"]],
        "workspace_dir": str(workspace_dir),
        "opencode_plan": plan,
        "hub_context_hash": hashlib.sha256(hub_context.encode("utf-8")).hexdigest(),
    }

    assert rag_context["chunk_count"] >= 3
    assert "SecurityController" in evidence["rag_symbols"]
    assert "TokenVerifier" in evidence["rag_symbols"]
    assert "PolicyService" in evidence["rag_symbols"]
    assert "ref.java.keycloak" in hub_context
    assert "mini_rag_fixture_index" in hub_context
    assert "SecurityController.java" in hub_context
    assert "TokenVerifier.java" in hub_context
    assert "PolicyService.java" in hub_context
    assert plan["schema"] == "command_plan_artifact.v1"
    assert plan["required_approval"] is True
    assert plan["risk_classification"] == "high"
    assert "No direct execution" in " ".join(plan["expected_effects"])
    assert evidence["hub_context_hash"]
