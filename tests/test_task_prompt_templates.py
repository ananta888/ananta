import uuid
from pathlib import Path

from agent.db_models import GoalDB, RoleDB, TaskDB, TeamDB, TemplateDB
from agent.services.repository_registry import get_repository_registry
from agent.services.task_scoped_execution_service import TaskScopedExecutionService


def test_system_prompt_uses_team_role_template_and_goal(app):
    with app.app_context():
        repos = get_repository_registry()
        role_template = repos.template_repo.save(
            TemplateDB(name="Classic Developer", prompt_template="classic {{team_goal}}")
        )
        team_template = repos.template_repo.save(
            TemplateDB(
                name="OpenCode Developer",
                prompt_template="team={{team_name}} role={{role_name}} goal={{team_goal}}",
            )
        )
        role = repos.role_repo.save(
            RoleDB(name="OpenCode Developer Role", description="developer", default_template_id=role_template.id)
        )
        team = repos.team_repo.save(
            TeamDB(name="Delivery Team", description="Build product", role_templates={role.id: team_template.id})
        )
        goal = repos.goal_repo.save(GoalDB(goal="Ship artifact sync", team_id=team.id))
        task = repos.task_repo.save(
            TaskDB(
                id=str(uuid.uuid4()),
                title="Implement artifact flow",
                description="Wire artifact sync",
                team_id=team.id,
                assigned_role_id=role.id,
                goal_id=goal.id,
            )
        )

        prompt = TaskScopedExecutionService()._get_system_prompt_for_task(task.id)

    assert prompt == "team=Delivery Team role=OpenCode Developer Role goal=Ship artifact sync"


def test_task_prompt_materializes_workspace_context_files_and_keeps_prompt_short(app, tmp_path):
    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **dict(app.config.get("AGENT_CONFIG") or {}),
            "worker_runtime": {"workspace_root": str(tmp_path)},
        }
        task = {
            "id": "task-short-prompt",
            "title": "Use workspace files",
            "description": "Use AGENTS and research files from workspace",
            "worker_execution_context": {
                "context": {"context_text": "VERY LONG HUB CONTEXT"},
                "expected_output_schema": {"type": "object", "required": ["reason"]},
            },
        }
        prompt, meta = TaskScopedExecutionService()._build_task_propose_prompt(
            tid="task-short-prompt",
            task=task,
            base_prompt="Keep the prompt short and rely on workspace files.",
            tool_definitions_resolver=lambda allowlist=None: [{"name": "bash", "allowlist": allowlist or []}],
            research_context={
                "artifact_ids": ["artifact-1"],
                "knowledge_collection_ids": ["collection-1"],
                "repo_scope_refs": [],
                "prompt_section": "VERY LONG RESEARCH CONTEXT",
                "truncated": False,
                "context_char_count": 26,
            },
        )

    workspace_meta = ((meta.get("workspace") or {}).get("opencode_context_files") or {})
    workspace_dir = Path((meta.get("workspace") or {}).get("workspace_dir") or "")
    assert workspace_dir.exists()
    assert "VERY LONG HUB CONTEXT" not in prompt
    assert "VERY LONG RESEARCH CONTEXT" not in prompt
    assert "AGENTS.md" in prompt
    assert ".ananta/context-index.md" in prompt
    assert workspace_meta.get("agents_path") == "AGENTS.md"
    assert workspace_meta.get("task_brief_path") == ".ananta/task-brief.md"
    assert workspace_meta.get("response_contract_path") == ".ananta/response-contract.md"
    assert workspace_meta.get("research_context_prompt_path") == "rag_helper/research-context.md"
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / ".ananta" / "hub-context.md").read_text(encoding="utf-8").strip() == "VERY LONG HUB CONTEXT"
    assert (workspace_dir / "rag_helper" / "research-context.md").read_text(encoding="utf-8").strip() == "VERY LONG RESEARCH CONTEXT"
