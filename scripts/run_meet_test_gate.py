"""Run the fixed two-Worker reference gate under a pre-reserved Hub TEST identity."""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.hub_browser_test_evidence import HubBrowserTestRun
from scripts.meet_test_gate_inputs import ROOT_PATHS, frontend_digest, native_environment, snapshot_repository

ROOT = Path(__file__).resolve().parents[1]
NODE = (
    "tests/test_meet_multi_worker_containers.py::"
    "test_two_role_assigned_packaged_workers_share_owned_screens_and_stop_independently[room-reconnect-media]"
)
ENVIRONMENT = {
    "ANANTA_TEST_DATABASE_MODE": "wal",
    "ANANTA_SQLITE_POOL_SIZE": "8",
    "ANANTA_MEET_MEDIA_TIMING": "1",
    "MEET_MULTI_WORKER_GATE": "1",
    "MEET_WORKER_RESOURCES_GATE": "1",
}


def execute(command, environment, log_path, *, root=ROOT, timeout=420):
    with log_path.open("xb") as log:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=timeout)
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


def counts(path):
    cases = ET.parse(path).findall(".//testcase")
    return {
        "tests": len(cases),
        **{
            name: sum(case.find(tag) is not None for case in cases)
            for name, tag in (("failed", "failure"), ("errors", "error"), ("skipped", "skipped"))
        },
    }


def run(output, registry_db, meet_root, *, root=ROOT, reserve=HubBrowserTestRun.reserve, worker=execute):
    before, sources = snapshot_repository(root, ROOT_PATHS)
    companion, _ = snapshot_repository(meet_root, (".",))
    environment = native_environment()
    bundle = frontend_digest(environment["MEET_TEST_PUBLIC_DIR"])
    profile = {
        "schema": "ananta.meet-test-reference-profile.v1",
        "node": NODE,
        "environment": ENVIRONMENT,
        "timeout_seconds": 420,
        "reference": "two-cpu-one-gib-per-publisher-independent-media-v1",
    }
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    run = reserve(
        root=root,
        registry_db=registry_db,
        task_id="MAP-30",
        source_paths=sources,
        execution_profile=profile,
        environment={"companion": companion, "frontend_digest": bundle, "native": environment},
        policy_paths=(
            Path("AGENTS.md"),
            Path("docs/contracts/meet-live-media-clock.md"),
            Path("docs/contracts/meet-capacity-admission.md"),
        ),
    )
    # The IDs are issued by the Hub registry above, never constructed by this runner.
    print(
        json.dumps(
            {"state": "reserved", "source_id": run.source_id, "run_id": run.run_id, "scope": "test", "synthetic": True}
        ),
        flush=True,
    )
    result = {"passed": False, "reason": "meet_test_gate_incomplete"}
    started = time.monotonic()
    try:
        child_environment = (
            os.environ
            | ENVIRONMENT
            | {"ANANTA_HUB_EVIDENCE_ASSIGNMENT_JSON": json.dumps(run.assignment, sort_keys=True)}
        )
        code = worker(
            [
                sys.executable,
                "-m",
                "pytest",
                "-n",
                "0",
                "-q",
                NODE,
                "--junitxml=" + str(output / "junit.xml"),
                "-o",
                "cache_dir=" + str(output / "pytest-cache"),
            ],
            child_environment,
            output / "pytest.log",
            root=root,
        )
        observed = counts(output / "junit.xml")
        after, _ = snapshot_repository(root, ROOT_PATHS)
        companion_after, _ = snapshot_repository(meet_root, (".",))
        stable = (
            before == after
            and companion == companion_after
            and bundle == frontend_digest(environment["MEET_TEST_PUBLIC_DIR"])
        )
        passed = code == 0 and observed == {"tests": 1, "failed": 0, "errors": 0, "skipped": 0} and stable
        result = {
            "passed": passed,
            "returncode": code,
            "counts": observed,
            "inputs_unchanged": stable,
            "reason": "meet_test_gate_passed" if passed else "meet_test_gate_failed",
            "junit_digest": hashlib.sha256((output / "junit.xml").read_bytes()).hexdigest(),
        }
    except Exception as error:
        result = {"passed": False, "reason": "meet_test_gate_exception", "error_type": type(error).__name__}
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        identity = run.complete(result, succeeded=result["passed"])
        report = {
            "schema": "ananta.meet-test-reference-result.v1",
            "source": before,
            "companion": companion,
            "frontend_digest": bundle,
            "profile": profile,
            "result": result,
            "identity": identity,
        }
        with (output / "report.json").open("x") as target:
            json.dump(report, target, indent=2, sort_keys=True)
        print(
            json.dumps(
                {
                    "state": "completed",
                    "passed": result["passed"],
                    "report": str(output / "report.json"),
                    "production_release_eligible": False,
                }
            ),
            flush=True,
        )
    return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--registry-db", type=Path, default=ROOT / "data/meet-test-evidence.sqlite3")
    parser.add_argument("--meet-root", type=Path, default=ROOT.parent / "webrtc-minimize-server")
    args = parser.parse_args()
    return run(args.output_directory.resolve(), args.registry_db.resolve(), args.meet_root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
