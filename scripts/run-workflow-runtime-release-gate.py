#!/usr/bin/env python3
"""Run the versioned Native/LangGraph/Temporal workflow-runtime release gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.workflow_evaluation_service import (  # noqa: E402
    DEFAULT_EVALUATION_SUITE_PATH,
    load_workflow_evaluation_suite,
)
from agent.services.workflow_runtime.release_gate import (  # noqa: E402
    DEFAULT_WORKFLOW_RELEASE_GATE_PATH,
    EVIDENCE_BUILD_ID_ENV,
    EVIDENCE_COMMAND_HASH_ENV,
    EVIDENCE_COMMAND_ID_ENV,
    EVIDENCE_CONTRACT_HASH_ENV,
    EVIDENCE_PATH_ENV,
    EVIDENCE_REVISION_ENV,
    EVIDENCE_RUNTIME_ID_ENV,
    VerificationCommandResult,
    WorkflowRuntimeReleaseGate,
    load_runtime_release_evidence,
    load_runtime_release_evidence_jsonl,
    load_workflow_release_gate_config,
    release_verification_command_hash,
)


def _junit_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"tests": 0, "failures": 0, "errors": 1, "skipped": 0}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }


def _run_verification_commands(
    gate: WorkflowRuntimeReleaseGate,
    *,
    revision: str,
    build_id: str,
) -> tuple[tuple[VerificationCommandResult, ...], tuple]:
    environment = os.environ.copy()
    environment["PYTHONHASHSEED"] = "0"
    results: list[VerificationCommandResult] = []
    evidence: list = []
    with tempfile.TemporaryDirectory(prefix="ananta-workflow-release-") as temporary:
        temporary_path = Path(temporary)
        for command in gate.config.verification_commands:
            junit_path = temporary_path / f"{command.command_id}.xml"
            argv = list(command.argv)
            argv[0] = sys.executable
            argv.append(f"--junitxml={junit_path}")
            command_hash = release_verification_command_hash(command, gate.contract_hash)
            command_environment = dict(environment)
            evidence_path = temporary_path / f"{command.command_id}.evidence.jsonl"
            if command.evidence_runtime_ids:
                command_environment.update(
                    {
                        EVIDENCE_PATH_ENV: str(evidence_path),
                        EVIDENCE_CONTRACT_HASH_ENV: gate.contract_hash,
                        EVIDENCE_REVISION_ENV: revision,
                        EVIDENCE_BUILD_ID_ENV: build_id,
                        EVIDENCE_COMMAND_ID_ENV: command.command_id,
                        EVIDENCE_COMMAND_HASH_ENV: command_hash,
                        EVIDENCE_RUNTIME_ID_ENV: command.evidence_runtime_ids[0],
                    }
                )
            completed = subprocess.run(argv, cwd=ROOT, env=command_environment, check=False)
            counts = _junit_counts(junit_path)
            command_evidence = ()
            evidence_error = 0
            if command.evidence_runtime_ids:
                try:
                    command_evidence = load_runtime_release_evidence_jsonl(
                        evidence_path,
                        expected_contract_hash=gate.contract_hash,
                        expected_revision=revision,
                        expected_build_id=build_id,
                        expected_command_id=command.command_id,
                        expected_command_hash=command_hash,
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    evidence_error = 1
                else:
                    evidence.extend(command_evidence)
            results.append(
                VerificationCommandResult(
                    command_id=command.command_id,
                    argv=command.argv,
                    returncode=completed.returncode,
                    revision=revision,
                    build_id=build_id,
                    command_hash=command_hash,
                    evidence_records=len(command_evidence),
                    evidence_chain_head=(command_evidence[-1].record_hash if command_evidence else ""),
                    errors=counts["errors"] + evidence_error,
                    tests=counts["tests"],
                    failures=counts["failures"],
                    skipped=counts["skipped"],
                )
            )
    return tuple(results), tuple(evidence)


def _workspace_revision(
    root: Path,
    *,
    excluded_path: Path | None = None,
) -> str:
    """Bind local evidence to HEAD and every non-ignored workspace source byte."""

    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()
    pathspecs = ["."]
    if excluded_path is not None:
        try:
            relative_exclusion = excluded_path.resolve().relative_to(root.resolve())
        except ValueError:
            pass
        else:
            pathspecs.append(f":(exclude){relative_exclusion.as_posix()}")
    tracked_diff = subprocess.check_output(
        ["git", "diff", "--binary", "HEAD", "--", *pathspecs],
        cwd=root,
    )
    untracked_output = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=root,
    )
    untracked_paths = sorted(
        Path(raw.decode("utf-8", errors="surrogateescape")) for raw in untracked_output.split(b"\0") if raw
    )
    digest = hashlib.sha256()
    digest.update(b"tracked-diff\0")
    digest.update(tracked_diff)
    included_untracked = 0
    for relative_path in untracked_paths:
        candidate_path = root / relative_path
        if excluded_path is not None and candidate_path.absolute() == excluded_path.absolute():
            continue
        if candidate_path.is_symlink():
            content = os.readlink(candidate_path).encode("utf-8", errors="surrogateescape")
        elif candidate_path.is_file():
            content = candidate_path.read_bytes()
        else:
            continue
        included_untracked += 1
        digest.update(b"untracked-path\0")
        digest.update(relative_path.as_posix().encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0untracked-content\0")
        digest.update(content)
    if not tracked_diff and included_untracked == 0:
        return head
    return f"{head}+worktree.{digest.hexdigest()}"


def _release_identity(
    environment: dict[str, str] | None = None,
    *,
    output_path: Path | None = None,
) -> tuple[str, str]:
    source = os.environ if environment is None else environment
    revision = str(source.get(EVIDENCE_REVISION_ENV) or source.get("GITHUB_SHA") or "").strip()
    if not revision:
        revision = _workspace_revision(ROOT, excluded_path=output_path)
    build_id = str(
        source.get(EVIDENCE_BUILD_ID_ENV)
        or source.get("GITHUB_RUN_ID")
        or f"local-{hashlib.sha256(revision.encode('utf-8')).hexdigest()[:16]}"
    ).strip()
    if not revision or not build_id:
        raise ValueError("workflow_release_build_identity_missing")
    return revision, build_id


def _assert_workspace_snapshot_unchanged(
    expected_revision: str,
    *,
    root: Path = ROOT,
    excluded_path: Path | None = None,
) -> None:
    """Reject evidence when checked source bytes changed during verification."""

    if _workspace_revision(root, excluded_path=excluded_path) != expected_revision:
        raise ValueError("workflow_release_workspace_changed_during_verification")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, default=DEFAULT_WORKFLOW_RELEASE_GATE_PATH)
    parser.add_argument("--evaluation-suite", type=Path, default=DEFAULT_EVALUATION_SUITE_PATH)
    parser.add_argument(
        "--evidence-input",
        type=Path,
        help="explicit v2 per-run evidence manifest; accepted only with --no-commands",
    )
    parser.add_argument("--output", type=Path, help="optional stable machine-readable artifact path")
    parser.add_argument(
        "--no-commands",
        action="store_true",
        help="validate evidence only; deliberately produces a failed non-release artifact",
    )
    arguments = parser.parse_args(argv)
    try:
        suite = load_workflow_evaluation_suite(arguments.evaluation_suite)
        config = load_workflow_release_gate_config(arguments.gate)
        gate = WorkflowRuntimeReleaseGate(config=config, suite=suite)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    if arguments.no_commands:
        if arguments.evidence_input is None:
            parser.error("--no-commands requires --evidence-input")
        try:
            evidence = load_runtime_release_evidence(
                arguments.evidence_input,
                expected_contract_hash=gate.contract_hash,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        command_results: tuple[VerificationCommandResult, ...] = ()
    else:
        if arguments.evidence_input is not None:
            parser.error("--evidence-input cannot replace records emitted by default subprocess gates")
        try:
            workspace_snapshot = _workspace_revision(
                ROOT,
                excluded_path=arguments.output,
            )
            revision, build_id = _release_identity(output_path=arguments.output)
            command_results, evidence = _run_verification_commands(
                gate,
                revision=revision,
                build_id=build_id,
            )
            _assert_workspace_snapshot_unchanged(
                workspace_snapshot,
                excluded_path=arguments.output,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            parser.error(str(exc))
    result = gate.evaluate(evidence, command_results)
    payload = result.to_dict()
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    if arguments.output:
        _write_json(arguments.output, payload)
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
