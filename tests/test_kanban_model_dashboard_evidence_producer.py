from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.run_kanban_model_dashboard_evidence import (
    EVIDENCE_RELATIVE_DIR,
    REQUIRED_SUITES,
    SUITE_SPECS,
    CommandSpec,
    EvidenceProducer,
    EvidenceProducerError,
    ExecutionResult,
    _atomic_write_json,
    _validate_performance_result,
    _validate_playwright,
)

COMMIT = "a" * 40
NOW = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)


class _Candidate:
    def __init__(
        self,
        *,
        blobs: dict[str, bytes],
        current: str = COMMIT,
        extra_paths: set[str] | None = None,
    ) -> None:
        self.blobs = blobs
        self.current = current
        self.extra_paths = extra_paths or set()

    def current_commit(self) -> str:
        return self.current

    def path_exists(self, _commit_sha: str, relative_path: str) -> bool:
        return relative_path in self.blobs or relative_path in self.extra_paths

    def read_path(
        self,
        _commit_sha: str,
        relative_path: str,
        *,
        max_bytes: int,
    ) -> bytes | None:
        value = self.blobs.get(relative_path)
        if value is not None and len(value) > max_bytes:
            raise AssertionError("test blob unexpectedly oversized")
        return value


class _Executor:
    def __init__(self, results: list[ExecutionResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, **_kwargs) -> ExecutionResult:
        self.calls.append(tuple(argv))
        return self.results.pop(0)


def _minimal_spec(*, inputs: tuple[str, ...] = ("source.py",)) -> CommandSpec:
    del inputs
    return CommandSpec(
        argv=("{python}", "-m", "pytest", "-q", "source.py"),
        minimum_passed=1,
    )


def _producer(
    root: Path,
    candidate: _Candidate,
    executor: _Executor,
) -> EvidenceProducer:
    return EvidenceProducer(
        root=root,
        candidate_source=candidate,
        executor=executor,
        clock=lambda: NOW,
        monotonic=lambda: 1.0,
        python_executable="/trusted/python3",
        npx_executable="/trusted/npx",
    )


def _install_contract_spec(
    monkeypatch,
    *,
    inputs: tuple[str, ...] = ("source.py",),
) -> None:
    spec_type = type(SUITE_SPECS["contract"])
    monkeypatch.setitem(
        SUITE_SPECS,
        "contract",
        spec_type(
            suite="contract",
            commands=(_minimal_spec(inputs=inputs),),
            inputs=inputs,
        ),
    )


def test_producer_writes_verified_commit_bound_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_contract_spec(monkeypatch)
    source = b"print('candidate')\n"
    (tmp_path / "source.py").write_bytes(source)
    executor = _Executor(
        [ExecutionResult(exit_code=0, stdout=b"1 passed in 0.01s\n")]
    )

    evidence = _producer(
        tmp_path,
        _Candidate(blobs={"source.py": source}),
        executor,
    ).produce_suite(suite="contract", candidate_sha=COMMIT)

    output = tmp_path / EVIDENCE_RELATIVE_DIR / "contract.json"
    assert evidence["status"] == "passed"
    assert evidence["commit_sha"] == COMMIT
    assert evidence["candidate"]["input_blobs_verified"] is True
    assert evidence["input_hashes"]["source.py"] == hashlib.sha256(
        source
    ).hexdigest()
    assert evidence["commands"][0]["argv"] == [
        "/trusted/python3",
        "-m",
        "pytest",
        "-q",
        "source.py",
    ]
    assert evidence["commands"][0]["exit_code"] == 0
    assert output.is_file()
    assert not any(
        text.startswith(("RUN_", "SRC_"))
        for text in json.dumps(evidence).replace('"', " ").split()
    )


@pytest.mark.parametrize(
    ("actual", "candidate_blob", "reason"),
    [
        (None, b"candidate", "input_missing"),
        (b"changed", b"candidate", "input_hash_drift"),
        (b"local", None, "input_not_in_candidate"),
    ],
)
def test_producer_blocks_missing_stale_and_foreign_inputs(
    tmp_path: Path,
    monkeypatch,
    actual: bytes | None,
    candidate_blob: bytes | None,
    reason: str,
) -> None:
    _install_contract_spec(monkeypatch)
    if actual is not None:
        (tmp_path / "source.py").write_bytes(actual)
    blobs = {} if candidate_blob is None else {"source.py": candidate_blob}
    executor = _Executor([])

    evidence = _producer(
        tmp_path,
        _Candidate(blobs=blobs),
        executor,
    ).produce_suite(suite="contract", candidate_sha=COMMIT)

    assert evidence["status"] == "blocked"
    assert evidence["reason_codes"] == [reason]
    assert executor.calls == []


def test_producer_rejects_symlinked_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_contract_spec(monkeypatch)
    target = tmp_path / "target.py"
    target.write_text("candidate")
    (tmp_path / "source.py").symlink_to(target)

    evidence = _producer(
        tmp_path,
        _Candidate(blobs={"source.py": b"candidate"}),
        _Executor([]),
    ).produce_suite(suite="contract", candidate_sha=COMMIT)

    assert evidence["status"] == "blocked"
    assert evidence["reason_codes"] == ["input_symlink_unsafe"]


def test_command_failure_can_never_write_passed_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_contract_spec(monkeypatch)
    source = b"candidate"
    (tmp_path / "source.py").write_bytes(source)

    evidence = _producer(
        tmp_path,
        _Candidate(blobs={"source.py": source}),
        _Executor(
            [
                ExecutionResult(
                    exit_code=7,
                    stdout=b"0 passed",
                    stderr=b"failed",
                )
            ]
        ),
    ).produce_suite(suite="contract", candidate_sha=COMMIT)

    assert evidence["status"] == "failed"
    assert evidence["reason_codes"] == ["command_exit_nonzero"]
    assert evidence["commands"][0]["exit_code"] == 7
    assert evidence["commands"][0]["status"] == "failed"


def test_producer_rejects_both_sha_circularity_forms(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_relative = str(EVIDENCE_RELATIVE_DIR / "contract.json")
    _install_contract_spec(monkeypatch, inputs=(output_relative,))
    producer = _producer(
        tmp_path,
        _Candidate(blobs={output_relative: b"old"}),
        _Executor([]),
    )

    with pytest.raises(
        EvidenceProducerError,
        match="evidence_sha_circularity",
    ):
        producer.produce_suite(suite="contract", candidate_sha=COMMIT)

    _install_contract_spec(monkeypatch)
    (tmp_path / "source.py").write_bytes(b"candidate")
    producer = _producer(
        tmp_path,
        _Candidate(
            blobs={"source.py": b"candidate"},
            extra_paths={output_relative},
        ),
        _Executor([]),
    )
    with pytest.raises(
        EvidenceProducerError,
        match="evidence_output_in_candidate",
    ):
        producer.produce_suite(suite="contract", candidate_sha=COMMIT)


def test_atomic_writer_preserves_previous_file_on_partial_replace(
    tmp_path: Path,
) -> None:
    output = tmp_path / "evidence.json"
    output.write_text("previous\n")

    def fail_replace(_source, _target) -> None:
        raise OSError("injected replace failure")

    with pytest.raises(OSError, match="injected"):
        _atomic_write_json(output, {"status": "passed"}, replace=fail_replace)

    assert output.read_text() == "previous\n"
    assert list(tmp_path.glob(".evidence.json.*.tmp")) == []


def test_allowlist_contains_exactly_the_seven_release_suites() -> None:
    assert tuple(SUITE_SPECS) == REQUIRED_SUITES
    assert len(REQUIRED_SUITES) == 7
    accessibility = SUITE_SPECS["accessibility"].commands[0]
    assert dict(accessibility.env)["E2E_RESULTS_DIR"] == (
        "/tmp/ananta-kanban-model-dashboard-accessibility-v1"
    )
    assert dict(accessibility.env)["E2E_PORT"] == "4217"


def test_performance_gate_validator_reads_versioned_profile_projection() -> None:
    value = {
        "schema": "ananta.kanban-model-dashboard.performance-gate.v1",
        "status": "passed",
        "release_evidence": True,
        "formal_gate_eligible": True,
        "blockers": [],
        "commit": {"sha": COMMIT},
        "profile": {"sha256": "b" * 64},
        "environment": {},
        "measurements": {},
        "absolute_evaluation": {},
        "baseline_evaluation": {},
    }

    result = _validate_performance_result("performance_gate", value, COMMIT)

    assert result == {
        "schema": "ananta.kanban-model-dashboard.performance-gate.v1",
        "requirements_satisfied": True,
    }


def test_playwright_validator_extracts_one_report_from_harness_logs() -> None:
    spec = CommandSpec(
        argv=("{npx}", "playwright", "test"),
        validator="playwright",
        minimum_passed=4,
    )
    report = {
        "config": {
            "projects": [
                {"name": "chromium"},
                {"name": "firefox"},
            ]
        },
        "suites": [],
        "stats": {"expected": 4, "unexpected": 0, "skipped": 0},
    }
    stdout = (
        "hub startup log {not-json}\n"
        + json.dumps(report)
        + "\nworker shutdown log\n"
    ).encode()

    result = _validate_playwright(spec, stdout, b"")

    assert result == {
        "expected": 4,
        "unexpected": 0,
        "skipped": 0,
        "projects": ["chromium", "firefox"],
    }
