from __future__ import annotations
import time, uuid
from dataclasses import dataclass, field
from typing import Any

@dataclass
class TestSelectionResult:
    selection_id: str
    run_id: str
    proposed_commands: list[str]
    relevant_test_files: list[str]
    selection_reason: str
    flaky_risk: str  # "low"|"medium"|"high"
    created_at: float

@dataclass
class TestReport:
    report_id: str
    run_id: str
    command_used: str
    exit_code: int
    duration_ms: int
    log_excerpt: str
    artifact_paths: list[str]
    status: str  # "passed"|"failed"|"timeout"|"flaky"|"not_run"
    flaky: bool
    timeout: bool
    blocks_apply: bool
    created_at: float
    def as_dict(self) -> dict[str, Any]:
        return {"report_id": self.report_id, "status": self.status, "exit_code": self.exit_code,
                "blocks_apply": self.blocks_apply}

class TesterExpert:
    def select_tests(self, *, run_id: str, changed_files: list[str],
                    available_test_files: list[str] | None = None) -> TestSelectionResult:
        relevant = [f for f in (available_test_files or []) if any(
            cf.replace(".py","") in f or f.replace("test_","") in cf for cf in changed_files
        )]
        return TestSelectionResult(
            selection_id=str(uuid.uuid4()), run_id=run_id,
            proposed_commands=["python -m pytest " + " ".join(relevant or ["tests/"])],
            relevant_test_files=relevant or [],
            selection_reason=f"Selected based on {len(changed_files)} changed files",
            flaky_risk="low", created_at=time.time(),
        )

    def create_report(self, *, run_id: str, command: str, exit_code: int, duration_ms: int,
                     log_output: str = "", artifact_paths: list[str] | None = None,
                     flaky: bool = False, timeout: bool = False,
                     policy_blocks_on_failure: bool = True) -> TestReport:
        if timeout:
            status = "timeout"
        elif flaky:
            status = "flaky"
        elif exit_code == 0:
            status = "passed"
        else:
            status = "failed"
        return TestReport(
            report_id=str(uuid.uuid4()), run_id=run_id, command_used=command,
            exit_code=exit_code, duration_ms=duration_ms, log_excerpt=log_output[:500],
            artifact_paths=list(artifact_paths or []), status=status, flaky=flaky, timeout=timeout,
            blocks_apply=(status == "failed" and policy_blocks_on_failure),
            created_at=time.time(),
        )

    def report_not_run(self, run_id: str) -> TestReport:
        return TestReport(
            report_id=str(uuid.uuid4()), run_id=run_id, command_used="",
            exit_code=-1, duration_ms=0, log_excerpt="Tests not run",
            artifact_paths=[], status="not_run", flaky=False, timeout=False,
            blocks_apply=False, created_at=time.time(),
        )
