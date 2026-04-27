from __future__ import annotations

from pathlib import Path

from agent.services.reference_profile_service import ReferenceProfileService
from agent.services.task_scoped_execution_service import TaskScopedExecutionService
from worker.adapters.opencode_adapter import OpenCodeAdapter


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "java_security_mini"


def _java_reference_chunks() -> list[dict[str, str]]:
    chunks = []
    for source in sorted(FIXTURE_ROOT.rglob("*.java")):
        chunks.append(
            {
                "path": str(source.relative_to(FIXTURE_ROOT)),
                "symbol": source.stem,
                "text": source.read_text(encoding="utf-8")[:1400],
            }
        )
    return chunks


def _reference_context_text() -> str:
    service = ReferenceProfileService()
    plan = service.build_mode_reference_plan(
        flow="new_project",
        mode_data={
            "preferred_stack": "Java",
            "project_idea": "OIDC token validation with issuer policy and admin authorization",
            "platform": "security backend service",
        },
    )
    selected = plan["selection"]["selected_profile"]
    assert selected["profile_id"] == "ref.java.keycloak"
    chunk_text = "\n\n".join(
        f"SOURCE: {chunk['path']}\nSYMBOL: {chunk['symbol']}\n{chunk['text']}" for chunk in _java_reference_chunks()
    )
    return (
        f"Reference profile: {selected['profile_id']}\n"
        f"Reference repo: {selected['reference_source']['repo']}\n"
        "Boundary: guidance_not_clone; no blind copy.\n\n"
        f"Mini Java reference chunks:\n{chunk_text}"
    )


def test_ananta_worker_prompt_path_includes_keycloak_reference_and_java_chunks(app, tmp_path):
    reference_context = _reference_context_text()

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **dict(app.config.get("AGENT_CONFIG") or {}),
            "worker_runtime": {"workspace_root": str(tmp_path)},
        }
        task = {
            "id": "task-java-worker-reference",
            "title": "Build Java security backend",
            "description": "Create a Java OIDC token validation and admin authorization backend.",
            "task_kind": "coding",
            "preferred_bundle_mode": "standard",
            "required_capabilities": ["coding", "java", "security"],
            "worker_execution_context": {
                "context": {
                    "context_text": reference_context,
                    "reference_profile_id": "ref.java.keycloak",
                    "reference_source_repo": "keycloak/keycloak",
                },
                "expected_output_schema": {
                    "type": "object",
                    "required": ["reason", "command", "boundary_notes"],
                },
            },
        }

        prompt, meta = TaskScopedExecutionService()._build_task_propose_prompt(
            tid=task["id"],
            task=task,
            base_prompt="Use the Java reference context to propose a safe implementation plan.",
            tool_definitions_resolver=lambda allowlist=None: [{"name": "bash", "allowlist": allowlist or []}],
            research_context=None,
        )

    workspace = meta["workspace"]
    workspace_dir = Path(workspace["workspace_dir"])
    hub_context = (workspace_dir / ".ananta" / "hub-context.md").read_text(encoding="utf-8")

    assert "AGENTS.md" in prompt
    assert ".ananta/hub-context.md" in prompt or ".ananta/context-index.md" in prompt
    assert workspace["opencode_context_files"]["hub_context_path"] == ".ananta/hub-context.md"
    assert "ref.java.keycloak" in hub_context
    assert "keycloak/keycloak" in hub_context
    assert "TokenVerifier.java" in hub_context
    assert "PolicyService.java" in hub_context
    assert "AdminResource.java" in hub_context
    assert "guidance_not_clone" in hub_context


def test_opencode_adapter_path_receives_keycloak_java_reference_prompt(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/opencode" if name == "opencode" else None)

    prompt = (
        "Implement a Java OIDC backend using reference profile ref.java.keycloak.\n"
        "Use TokenVerifier, PolicyService and AdminResource as mini reference chunks.\n"
        "Boundary: guidance_not_clone; no blind copy."
    )
    adapter = OpenCodeAdapter(enabled=True)

    descriptor = adapter.descriptor()
    plan = adapter.plan(task_id="task-java-opencode-reference", capability_id="coding", prompt=prompt)

    assert descriptor.adapter_id == "adapter.opencode"
    assert descriptor.enabled is True
    assert plan["schema"] == "command_plan_artifact.v1"
    assert plan["task_id"] == "task-java-opencode-reference"
    assert plan["capability_id"] == "coding"
    assert plan["required_approval"] is True
    assert plan["risk_classification"] == "high"
    assert "Experimental OpenCode adapter plan" in plan["explanation"]
    assert "ref.java.keycloak" in plan["explanation"]
    assert "No direct execution" in " ".join(plan["expected_effects"])
