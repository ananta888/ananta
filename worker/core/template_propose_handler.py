"""TemplateProposeHandler — FA-T007 deterministic baseline for new_software_project."""
from __future__ import annotations

from typing import Any

from worker.core.propose import ExecutableProposal


class TemplateProposeHandler:
    """Deterministic handler for new_software_project baseline."""

    def propose(
        self,
        *,
        tid: str,
        task: dict,
        task_kind: str,
        request_data: dict,
        base_prompt: str,
        service: Any,
        cli_runner: Any,
        forwarder: Any,
        tool_definitions_resolver: Any,
        handler_descriptor: dict,
    ) -> ExecutableProposal:
        project_name = task["title"].strip().lower().replace(" ", "-").replace("'", "")
        escaped_title = task["title"].replace("'", "'\\''")
        readme_content = f"# {task['title']}\n\nInitial template structure for new software project.\n"
        main_py_content = (
            "def main():\n"
            f"    print(\"Hello from {task['title']}!\")\n\n"
            "if __name__ == \"__main__\":\n"
            "    main()\n"
        )
        command = (
            f"mkdir -p {project_name} && "
            "python3 -c "
            f"\"from pathlib import Path; p=Path({project_name!r}); "
            f"(p/'README.md').write_text({readme_content!r}, encoding='utf-8'); "
            f"(p/'main.py').write_text({main_py_content!r}, encoding='utf-8')\""
        )

        return ExecutableProposal.from_command(
            goal_id=task.get("goal_id", "unknown"),
            task_id=tid,
            strategy_id="template_propose_handler",
            command=command,
            expected_artifacts=[
                { "kind": "dir", "path": project_name },
                { "kind": "file", "path": f"{project_name}/README.md" },
                { "kind": "file", "path": f"{project_name}/main.py" },
            ],
            reason="applied_new_software_project_template_baseline",
            safety_flags={"requires_review": False},
        )
