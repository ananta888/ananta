from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent.models import TaskStepProposeRequest
from agent.services.reference_profile_service import ReferenceProfileService
from agent.services.task_scoped_execution_service import TaskScopedExecutionService
from worker.adapters.opencode_adapter import OpenCodeAdapter


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "java_security_mini"
LIVE_REFERENCE_FLAG = "RUN_LIVE_REFERENCE_FLOW_TESTS"
LIVE_LLM_PROVIDER_ENV = "LIVE_LLM_PROVIDER"
LIVE_LLM_MODEL_ENV = "LIVE_LLM_MODEL"
LIVE_LLM_TIMEOUT_ENV = "LIVE_LLM_TIMEOUT_SEC"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
LIVE_OPENCODE_FLAG = "RUN_LIVE_OPENCODE_TESTS"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


def _require_live_reference_flow() -> dict[str, str | int]:
    if str(os.environ.get(LIVE_REFERENCE_FLAG) or "").strip() != "1":
        pytest.skip(f"Requires {LIVE_REFERENCE_FLAG}=1.")
    provider = str(os.environ.get(LIVE_LLM_PROVIDER_ENV) or "openai").strip().lower()
    if provider != "openai":
        pytest.skip(f"Requires {LIVE_LLM_PROVIDER_ENV}=openai for hosted live reference test.")
    api_key = str(os.environ.get(OPENAI_API_KEY_ENV) or "").strip()
    if not api_key:
        pytest.skip(f"Requires {OPENAI_API_KEY_ENV}.")
    return {
        "provider": provider,
        "model": str(os.environ.get(LIVE_LLM_MODEL_ENV) or DEFAULT_OPENAI_MODEL).strip(),
        "api_key": api_key,
        "timeout": int(str(os.environ.get(LIVE_LLM_TIMEOUT_ENV) or "45").strip()),
    }


def _require_live_opencode() -> str:
    if str(os.environ.get(LIVE_OPENCODE_FLAG) or "").strip() != "1":
        pytest.skip(f"Requires {LIVE_OPENCODE_FLAG}=1.")
    opencode = shutil.which("opencode")
    if not opencode:
        pytest.skip("Requires opencode binary on PATH.")
    return opencode


def _java_reference_context_text() -> str:
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
    chunks = []
    for source in sorted(FIXTURE_ROOT.rglob("*.java")):
        chunks.append(
            f"SOURCE: {source.relative_to(FIXTURE_ROOT)}\nSYMBOL: {source.stem}\n"
            f"{source.read_text(encoding='utf-8')[:1200]}"
        )
    return (
        f"Reference profile: {selected['profile_id']}\n"
        f"Reference repo: {selected['reference_source']['repo']}\n"
        "Boundary: guidance_not_clone; no blind copy.\n\n"
        + "\n\n".join(chunks)
    )


def _parse_json_object(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def test_live_ananta_worker_task_proposal_uses_real_llm_with_keycloak_java_reference(app, tmp_path):
    runtime = _require_live_reference_flow()

    with app.app_context():
        app.config["OPENAI_API_KEY"] = runtime["api_key"]
        app.config["AGENT_CONFIG"] = {
            **dict(app.config.get("AGENT_CONFIG") or {}),
            "default_provider": "openai",
            "default_model": runtime["model"],
            "worker_runtime": {"workspace_root": str(tmp_path)},
            "task_kind_execution_policies": {
                "coding": {"preferred_backend": "openai", "command_timeout": int(runtime["timeout"])}
            },
        }
        task = {
            "id": "task-live-java-worker-reference",
            "title": "Live Java security backend proposal",
            "description": "Use Java reference context to propose token validation, issuer policy and admin authorization components.",
            "task_kind": "coding",
            "required_capabilities": ["coding", "java", "security"],
            "worker_execution_context": {
                "context": {
                    "context_text": _java_reference_context_text(),
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
            base_prompt=(
                "Return JSON only. Recommend a safe Java implementation plan. "
                "Mention TokenVerifier, PolicyService and AdminResource if relevant."
            ),
            tool_definitions_resolver=lambda allowlist=None: [{"name": "bash", "allowlist": allowlist or []}],
            research_context=None,
        )

        from agent.llm_integration import generate_text

        response = generate_text(
            prompt=prompt + "\nReturn compact JSON only, below 180 words.",
            provider="openai",
            model=str(runtime["model"]),
            api_key=str(runtime["api_key"]),
            timeout=int(runtime["timeout"]),
            temperature=0,
            max_output_tokens=260,
        )

    payload = _parse_json_object(str(response or ""))
    joined = json.dumps(payload).lower()
    workspace_dir = Path(meta["workspace"]["workspace_dir"])

    assert workspace_dir.exists()
    assert payload
    assert "token" in joined
    assert "policy" in joined or "issuer" in joined
    assert "admin" in joined
    assert "ref.java.keycloak" in (workspace_dir / ".ananta" / "hub-context.md").read_text(encoding="utf-8")


def test_live_opencode_binary_and_adapter_plan_for_keycloak_java_reference(tmp_path):
    opencode = _require_live_opencode()

    version_result = subprocess.run(
        [opencode, "--version"],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if version_result.returncode != 0:
        help_result = subprocess.run(
            [opencode, "--help"],
            cwd=tmp_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        assert help_result.returncode == 0, help_result.stderr or help_result.stdout

    prompt = (
        "Use reference profile ref.java.keycloak for a Java OIDC/security backend. "
        "Use mini chunks TokenVerifier, PolicyService and AdminResource only as guidance. "
        "Do not copy Keycloak code."
    )
    adapter = OpenCodeAdapter(enabled=True)
    descriptor = adapter.descriptor()
    plan = adapter.plan(task_id="task-live-opencode-java-reference", capability_id="coding", prompt=prompt)

    assert descriptor.enabled is True
    assert descriptor.reason == "ready"
    assert plan["schema"] == "command_plan_artifact.v1"
    assert plan["required_approval"] is True
    assert plan["risk_classification"] == "high"
    assert "ref.java.keycloak" in plan["explanation"]
    assert "No direct execution" in " ".join(plan["expected_effects"])


def test_live_task_scoped_opencode_route_uses_real_opencode_detection(app, tmp_path, monkeypatch):
    _require_live_opencode()

    captured = {}

    def live_safe_cli_runner(**kwargs):
        captured.update(kwargs)
        return (
            0,
            json.dumps(
                {
                    "reason": "OpenCode route selected for Java reference planning.",
                    "command": "echo opencode-reference-plan",
                    "tool_calls": [],
                }
            ),
            "",
            "opencode",
        )

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **dict(app.config.get("AGENT_CONFIG") or {}),
            "worker_runtime": {"workspace_root": str(tmp_path)},
            "default_provider": "opencode",
            "task_kind_execution_policies": {"coding": {"preferred_backend": "opencode", "command_timeout": 60}},
        }
        from agent.db_models import TaskDB
        from agent.repository import task_repo

        task = task_repo.save(
            TaskDB(
                id="task-live-opencode-route-java-reference",
                title="Live OpenCode Java reference route",
                description="Use ref.java.keycloak with TokenVerifier, PolicyService and AdminResource for Java security planning.",
                task_kind="coding",
                required_capabilities=["coding", "java", "security"],
                preferred_bundle_mode="standard",
                worker_execution_context={
                    "context": {
                        "context_text": _java_reference_context_text(),
                        "reference_profile_id": "ref.java.keycloak",
                    }
                },
            )
        )
        response = TaskScopedExecutionService().propose_task_step(
            task.id,
            TaskStepProposeRequest(prompt="Return JSON plan for the Java reference task."),
            cli_runner=live_safe_cli_runner,
            forwarder=lambda *args, **kwargs: None,
            tool_definitions_resolver=lambda allowlist=None: [],
        )

    assert response.code == 200
    assert captured["backend"] == "opencode"
    assert captured["workdir"]
    assert "ref.java.keycloak" in captured["prompt"] or Path(captured["workdir"], ".ananta", "hub-context.md").read_text(encoding="utf-8").find("ref.java.keycloak") >= 0
    assert response.data["backend"] == "opencode"
    assert response.data["routing"]["effective_backend"] == "opencode"
