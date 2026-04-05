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
                title="Implement artifact flow",
                description="Wire artifact sync",
                team_id=team.id,
                assigned_role_id=role.id,
                goal_id=goal.id,
            )
        )

        prompt = TaskScopedExecutionService()._get_system_prompt_for_task(task.id)

    assert prompt == "team=Delivery Team role=OpenCode Developer Role goal=Ship artifact sync"
