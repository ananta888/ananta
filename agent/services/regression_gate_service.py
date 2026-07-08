"""Regression and output-diff gate for performance candidates."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from worker.shell.command_policy import classify_command


class RegressionGateService:
    def evaluate(
        self,
        *,
        workspace_dir: str | Path,
        test_commands: list[str] | None = None,
        expected_output: str | None = None,
        actual_output: str | None = None,
        output_diff_mode: str = "normalized",
    ) -> dict[str, Any]:
        commands = list(test_commands or [])
        results = []
        for command in commands:
            decision = classify_command(
                command=command,
                policy={
                    "allowlist": ["python", "pytest", "echo"],
                    "approval_required_commands": ["rm", "mv", "chmod", "chown", "sudo"],
                    "denylist_tokens": ["rm -rf /", "mkfs", ":(){"],
                },
                hub_policy_decision="allow",
            )
            if decision.classification != "safe":
                results.append({
                    "command": command,
                    "exit_code": 1,
                    "stdout_ref": "",
                    "stderr_ref": decision.reason,
                    "status": "failed",
                    "reason_code": "policy_denied",
                })
                continue
            completed = subprocess.run(
                command,
                cwd=Path(workspace_dir),
                shell=True,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            results.append({
                "command": command,
                "exit_code": completed.returncode,
                "stdout_ref": completed.stdout[:4000],
                "stderr_ref": completed.stderr[:4000],
                "status": "passed" if completed.returncode == 0 else "failed",
            })
        output_ok = self._compare_output(expected_output, actual_output, output_diff_mode)
        if not commands and expected_output is None:
            status = "inconclusive"
            reason = "missing_regressions"
        elif any(item["status"] != "passed" for item in results):
            status = "rejected"
            reason = "test_regression_failed"
        elif not output_ok:
            status = "rejected"
            reason = "output_diff_failed"
        else:
            status = "candidate_passed"
            reason = "regressions_passed"
        return {
            "schema": "regression_gate_result.v1",
            "status": status,
            "reason_code": reason,
            "test_results": results,
            "output_diff": {"mode": output_diff_mode, "passed": output_ok},
            "verification_artifact": {
                "schema": "verification_artifact.v1",
                "status": "passed" if status == "candidate_passed" else status,
                "test_results": results,
            },
        }

    @staticmethod
    def _compare_output(expected: str | None, actual: str | None, mode: str) -> bool:
        if expected is None:
            return True
        if actual is None:
            return False
        if mode == "strict":
            return expected == actual
        return " ".join(expected.split()) == " ".join(actual.split())


def get_regression_gate_service() -> RegressionGateService:
    return RegressionGateService()
