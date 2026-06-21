"""DetCommandTaskHandler — executes deterministic canvas steps via local subprocess.

Registered for task_kinds: run_tests, script, git_op, file_check, regex_check, fork, join.
Reads det_command / det_subtype from task.metadata (set by canvas toVpGraph()).
"""
from __future__ import annotations

import re
import shlex
import subprocess
import time
from typing import Any

from worker.core.propose import ExecutableProposal


class DetCommandTaskHandler:
    """Handles deterministic step execution. Conforms to TaskHandler protocol."""

    _BLOCKED = [
        re.compile(p, re.IGNORECASE)
        for p in [r"rm\s+-[rf]", r"\bdd\b.*of=", r"\bmkfs\b", r":\(\)\{", r"\bsudo\b",
                  r"curl\s+.*\|\s*(ba)?sh", r"wget\s+.*\|\s*(ba)?sh"]
    ]

    def propose(self, *, tid: str, task: dict, task_kind: str, **kwargs: Any) -> ExecutableProposal | None:
        meta: dict = task.get("metadata") or {}
        command: str = str(meta.get("det_command") or "").strip()
        subtype: str = str(meta.get("det_subtype") or task_kind or "script").strip()
        expected: str = str(meta.get("det_expected") or "").strip()

        # For fork/join/approval: emit advisory only
        if task_kind in ("fork", "join", "approval"):
            return ExecutableProposal(
                proposal_id=f"det-{tid}-{task_kind}",
                goal_id=task.get("goal_id", ""),
                task_id=tid,
                strategy_id="deterministic_handler",
                command=None,
                tool_calls=[],
                expected_artifacts=[],
                metadata={"task_kind": task_kind, "det_type": "control_flow"},
            )

        if not command:
            return None

        for blocked in self._BLOCKED:
            if blocked.search(command):
                return None

        result = self._run(subtype, command, expected)
        success = result.get("success", False)

        return ExecutableProposal(
            proposal_id=f"det-{tid}-{task_kind}",
            goal_id=task.get("goal_id", ""),
            task_id=tid,
            strategy_id="deterministic_handler",
            command=command,
            tool_calls=[],
            expected_artifacts=[],
            metadata={
                "task_kind": task_kind,
                "subtype": subtype,
                "result": result,
                "success": success,
                "exit_code": result.get("exit_code"),
                "stdout": result.get("stdout", "")[:1024],
                "stderr": result.get("stderr", "")[:512],
            },
        )

    def execute(self, **kwargs: Any) -> Any:
        return None

    def _run(self, subtype: str, command: str, expected: str, timeout: int = 10) -> dict:
        t0 = time.monotonic()
        try:
            if subtype == "api-call":
                if not command.startswith("http"):
                    return {"success": False, "error": "not_a_url"}
                cmd = f"curl -s -o /dev/null -w '%{{http_code}}' --max-time {timeout} {shlex.quote(command)}"
            elif subtype == "file-check":
                cmd = f"test -e {shlex.quote(command)} && echo EXISTS || echo MISSING"
            elif subtype == "python":
                cmd = f"python3 -c {shlex.quote(command)}"
            else:
                cmd = command

            r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                               timeout=timeout, cwd="/project-workspaces")
            duration_ms = int((time.monotonic() - t0) * 1000)
            success = r.returncode == 0
            stdout = r.stdout[:2048]

            if expected:
                if subtype == "regex-check":
                    success = bool(re.search(expected, stdout, re.MULTILINE))
                elif subtype == "api-call":
                    success = stdout.strip() == expected.strip()
                elif expected.startswith("exit "):
                    try:
                        success = r.returncode == int(expected.split()[1])
                    except (ValueError, IndexError):
                        pass

            return {"success": success, "exit_code": r.returncode,
                    "stdout": stdout, "stderr": r.stderr[:512], "duration_ms": duration_ms}
        except subprocess.TimeoutExpired:
            return {"success": False, "exit_code": -1, "stdout": "", "stderr": "Timeout", "duration_ms": timeout * 1000}
        except Exception as exc:
            return {"success": False, "exit_code": -1, "stdout": "", "stderr": str(exc), "duration_ms": 0}
