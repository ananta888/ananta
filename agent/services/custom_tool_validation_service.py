"""HDE-017: test validation for custom tool proposals.

A proposal can only become ``validated`` when its embedded test cases
ran successfully in an isolated temporary workspace. At least one
positive and one negative/error case are mandatory — a tool without
tests is never validated. Each case may stage setup files, then asserts
exit code, expected/forbidden output fragments and the allowed file
mutations. The resulting ``custom_tool_validation_report.v1`` is stored
as an artifact and referenced from the proposal (HDE-015 uses the
report + digest to gate promotion).
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from agent.services.custom_tool_executor import CustomToolExecutor

VALIDATION_REPORT_SCHEMA = "custom_tool_validation_report.v1"


def _default_data_root() -> Path:
    from agent.services.custom_tool_proposal_service import _default_data_root as proposals_root

    return proposals_root()


class CustomToolValidationService:
    """Runs proposal test cases in isolated temp workspaces (HDE-017)."""

    def __init__(self, data_root: Path | str | None = None) -> None:
        self._data_root = Path(data_root) if data_root else _default_data_root()

    @property
    def reports_dir(self) -> Path:
        return self._data_root / "tool-proposals" / "reports"

    def validate_proposal(self, proposal: dict[str, Any]) -> tuple[bool, str | None, dict[str, Any]]:
        """Returns (passed, report_ref, report)."""
        digest = str(proposal.get("proposal_digest") or "").strip()
        tests = [case for case in (proposal.get("tests") or []) if isinstance(case, dict)]
        kinds = {str(case.get("kind") or "") for case in tests}
        report: dict[str, Any] = {
            "schema": VALIDATION_REPORT_SCHEMA,
            "proposal_digest": digest,
            "tool_name": proposal.get("name"),
            "started_at": time.time(),
            "cases": [],
            "passed": False,
        }
        if not tests or "positive" not in kinds or "negative" not in kinds:
            report["error"] = "tests_missing_positive_or_negative_case"
            return False, self._store_report(digest, report), report

        executor = CustomToolExecutor(self._data_root)
        all_passed = True
        for case in tests:
            case_result = self._run_case(executor, proposal, case)
            report["cases"].append(case_result)
            all_passed = all_passed and bool(case_result.get("passed"))
        report["passed"] = all_passed
        report["finished_at"] = time.time()
        return all_passed, self._store_report(digest, report), report

    def _run_case(self, executor: CustomToolExecutor, proposal: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
        name = str(case.get("name") or case.get("kind") or "case")
        outcome: dict[str, Any] = {"name": name, "kind": case.get("kind"), "passed": False, "failures": []}
        with tempfile.TemporaryDirectory(prefix="ananta-tool-validation-") as tmp:
            workspace = Path(tmp)
            for rel_path, content in dict(case.get("setup_files") or {}).items():
                target = (workspace / str(rel_path)).resolve()
                if not str(target).startswith(str(workspace.resolve())):
                    outcome["failures"].append(f"setup_file_outside_workspace:{rel_path}")
                    return outcome
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(content), encoding="utf-8")

            result = executor.execute_spec(
                spec=dict(proposal),
                arguments=dict(case.get("arguments") or {}),
                workspace_dir=str(workspace),
                tool_call_id=f"validation-{name}",
                config={},
            )
            outcome["result_status"] = result.get("status")
            outcome["result_error"] = result.get("error")
            data = dict(result.get("data") or {})
            output = "\n".join(str(row.get("excerpt") or "") for row in (result.get("evidence") or []))

            expect_status = str(case.get("expect_status") or "").strip()
            if expect_status and str(result.get("status")) != expect_status:
                outcome["failures"].append(f"status_mismatch:{result.get('status')}!={expect_status}")
            if "expect_exit_code" in case and data.get("exit_code") != case.get("expect_exit_code"):
                outcome["failures"].append(f"exit_code_mismatch:{data.get('exit_code')}!={case.get('expect_exit_code')}")
            for fragment in case.get("expect_output_contains") or []:
                if str(fragment) not in output:
                    outcome["failures"].append(f"missing_output_fragment:{fragment}")
            for fragment in case.get("expect_output_not_contains") or []:
                if str(fragment) in output:
                    outcome["failures"].append(f"forbidden_output_fragment:{fragment}")
            expected_changes = sorted(str(p) for p in (case.get("expect_changed_paths") or []))
            actual_changes = sorted(str(p) for p in (data.get("changed_paths") or []))
            if expected_changes != actual_changes and ("expect_changed_paths" in case or actual_changes):
                outcome["failures"].append(f"changed_paths_mismatch:{actual_changes}!={expected_changes}")

        outcome["passed"] = not outcome["failures"]
        return outcome

    def _store_report(self, digest: str, report: dict[str, Any]) -> str | None:
        if not digest:
            return None
        try:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            path = self.reports_dir / f"{digest}.json"
            path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True), encoding="utf-8")
            return f"tool-proposals/reports/{digest}.json"
        except OSError:
            return None


custom_tool_validation_service: CustomToolValidationService | None = None


def get_custom_tool_validation_service() -> CustomToolValidationService:
    global custom_tool_validation_service
    if custom_tool_validation_service is None:
        custom_tool_validation_service = CustomToolValidationService()
    return custom_tool_validation_service
