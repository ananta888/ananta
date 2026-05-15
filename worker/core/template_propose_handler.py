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
        command = (
            f"mkdir -p {project_name} && "
            f"cd {project_name} && "
            f"touch README.md && "
            f"echo '# {escaped_title}' >> README.md && "
            f"echo '' >> README.md && "
            f"echo 'Initial template structure for new software project.' >> README.md && "
            f"touch main.py && "
            f"printf 'def main():\\n    print(\"Hello from %s!\")\\n\\nif __name__ == \"__main__\":\\n    main()\\n' '{escaped_title}' >> main.py && "
            f"git init && git add . && git commit -m 'feat: initial {project_name} template structure'"
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