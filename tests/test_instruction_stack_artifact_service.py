from __future__ import annotations

from agent.db_models import RoleDB, TaskDB, TemplateDB
from agent.services.instruction_layer_service import InstructionLayerService
from agent.services.instruction_stack_artifact_service import get_instruction_stack_artifact_service
from agent.services.repository_registry import get_repository_registry


def test_instruction_stack_artifact_checksum_is_deterministic():
    service = get_instruction_stack_artifact_service()
    payload = {
        "task_id": "task-1",
        "goal_id": "goal-1",
        "role_template_context": {"template_id": "tpl-1", "template_name": "Template A"},
        "applied_layers": [{"layer": "governance"}, {"layer": "task_overlay"}],
        "suppressed_layers": [{"layer": "user_profile", "reason": "forbidden_instruction_scope"}],
        "rendered_system_prompt": "System prompt",
        "diagnostics": {"version": "instruction-stack-v1"},
    }
    one = service.build_artifact(**payload)
    two = service.build_artifact(**payload)
    assert one.checksum == two.checksum


def test_assemble_for_task_includes_role_template_layer_and_prompt(app):
    with app.app_context():
        repos = get_repository_registry()
        template = repos.template_repo.save(
            TemplateDB(
                name="instruction-stack-role-template",
                description="Role template for stack integration test",
                prompt_template="Always provide explicit acceptance criteria.",
            )
        )
        role = repos.role_repo.save(
            RoleDB(
                name="Stack Integration Role",
                default_template_id=template.id,
            )
        )
        repos.task_repo.save(
            TaskDB(
                id="stack-artifact-task",
                title="Instruction stack role prompt test",
                status="todo",
                assigned_role_id=role.id,
            )
        )
        assembled = InstructionLayerService().assemble_for_task(
            task={"id": "stack-artifact-task", "assigned_role_id": role.id},
            base_prompt="Build stack artifact",
            system_prompt="Governance policy block",
        )

    rendered = str(assembled.get("rendered_system_prompt") or "")
    stack = dict(assembled.get("instruction_stack") or {})
    layers = [str(item.get("layer") or "") for item in list(stack.get("applied_layers") or [])]
    assert "[ROLE TEMPLATE]" in rendered
    assert "Always provide explicit acceptance criteria." in rendered
    assert layers[:2] == ["governance", "blueprint_template"]
    assert str(stack.get("checksum") or "").strip()
