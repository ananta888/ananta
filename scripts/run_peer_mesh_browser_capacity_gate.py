#!/usr/bin/env python3
"""Run the real-browser mesh gate under a Hub-reserved test evidence identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from sqlmodel import SQLModel, create_engine

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend-angular"
TASK_ID = "DPM-MESH-004"
SOURCE_PATHS = (
    Path("frontend-angular/playwright.peer-mesh-capacity.config.ts"),
    Path("frontend-angular/tests/peer-mesh-browser-capacity.spec.ts"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry-db",
        type=Path,
        default=ROOT / "data/peer-mesh-evidence.sqlite3",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "artifacts/test-gates/peer-mesh-browser-capacity.json",
    )
    args = parser.parse_args()
    revision = _git_revision()
    source_digest = _files_digest(SOURCE_PATHS)
    policy_digest = _files_digest((Path("AGENTS.md"), Path("docs/decisions/ADR-decentralized-peer-overlay.md")))
    environment = _environment()
    environment_digest = _digest(environment)
    execution_profile_digest = _digest(
        {
            "schema": "ananta.peer-mesh-browser-profile.v1",
            "engines": ["chromium", "firefox"],
            "profiles": ["audio_only", "camera_720p", "screenshare"],
            "participant_count": 4,
            "headless": True,
        }
    )
    registry = _registry(args.registry_db)
    source = registry.register_source(
        tenant_id="ananta-local",
        project_id="ananta",
        origin_type="test_harness",
        origin_digest=_digest([str(path) for path in SOURCE_PATHS]),
        content_digest=source_digest,
        policy_digest=policy_digest,
        evidence_scope="test",
        synthetic=True,
    )
    assignment_id = f"assignment-{uuid.uuid4().hex}"
    dispatch_lease_id = f"lease-{uuid.uuid4().hex}"
    reserved = registry.reserve_run(
        tenant_id="ananta-local",
        project_id="ananta",
        task_id=TASK_ID,
        assignment_id=assignment_id,
        dispatch_lease_id=dispatch_lease_id,
        repository_revision=revision,
        input_digest=source_digest,
        execution_profile_digest=execution_profile_digest,
        environment_digest=environment_digest,
        source_ids=[source.source_id],
        evidence_scope="test",
        idempotency_key=f"peer-mesh-{revision[:12]}-{uuid.uuid4().hex}",
        synthetic=True,
    )
    assignment = registry.assignment_projection(
        tenant_id="ananta-local",
        project_id="ananta",
        run_id=reserved.run_id,
        task_id=TASK_ID,
        assignment_id=assignment_id,
        dispatch_lease_id=dispatch_lease_id,
    )
    with tempfile.TemporaryDirectory(prefix="ananta-peer-mesh-") as measurement_dir:
        env = os.environ.copy()
        env["ANANTA_HUB_EVIDENCE_ASSIGNMENT_JSON"] = json.dumps(assignment, sort_keys=True)
        env["ANANTA_PEER_MESH_MEASUREMENT_DIR"] = measurement_dir
        cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        completed = subprocess.run(
            ("npx", "playwright", "test", "--config", "playwright.peer-mesh-capacity.config.ts"),
            cwd=FRONTEND,
            env=env,
            check=False,
            text=True,
            timeout=180,
        )
        cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        measurements = _measurements(Path(measurement_dir))
    succeeded = completed.returncode == 0 and len(measurements) == 6
    result_payload = {
        "schema": "ananta.peer-mesh-browser-capacity-result.v1",
        "repository_revision": revision,
        "measurements": measurements,
        "host_environment": environment,
        "child_cpu_seconds": round(
            cpu_after.ru_utime + cpu_after.ru_stime - cpu_before.ru_utime - cpu_before.ru_stime,
            6,
        ),
        "expected_measurement_count": 6,
        "command_exit_code": completed.returncode,
    }
    result_digest = _digest(result_payload)
    recorded = registry.record_result(
        tenant_id="ananta-local",
        project_id="ananta",
        run_id=reserved.run_id,
        assignment_id=assignment_id,
        dispatch_lease_id=dispatch_lease_id,
        terminal_state="succeeded" if succeeded else "failed",
        result_digest=result_digest,
    )
    release_check = registry.verify_release_binding(
        tenant_id="ananta-local",
        project_id="ananta",
        run_id=recorded.run_id,
        required_scope="local",
        task_id=TASK_ID,
        repository_revision=revision,
        source_ids=[source.source_id],
    )
    report = {
        **result_payload,
        "status": "passed" if succeeded else "failed",
        "evidence": {
            "issuer": recorded.issuer,
            "source_id": source.source_id,
            "run_id": recorded.run_id,
            "scope": recorded.evidence_scope,
            "synthetic": recorded.synthetic,
            "binding_digest": recorded.binding_digest,
            "result_digest": result_digest,
            "production_release_eligible": release_check.verified,
            "production_release_reason": release_check.reason_code,
        },
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["evidence"], sort_keys=True))
    return 0 if succeeded and not release_check.verified else 1


def _registry(path: Path) -> HubEvidenceRegistryService:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(
        engine,
        tables=[HubSourceEvidenceIdentityDB.__table__, HubRunEvidenceIdentityDB.__table__],
    )
    return HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine))


def _measurements(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]


def _environment() -> dict[str, Any]:
    cpu_model = "unknown"
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        cpu_model = next(
            (
                line.split(":", 1)[1].strip()
                for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.startswith("model name") and ":" in line
            ),
            "unknown",
        )
    pages = os.sysconf("SC_PHYS_PAGES") if hasattr(os, "sysconf") else 0
    page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 0
    return {
        "platform": platform.platform(),
        "cpu_model": cpu_model,
        "logical_cpu_count": os.cpu_count(),
        "memory_bytes": pages * page_size,
        "python": platform.python_version(),
        "node": _command_output(("node", "--version")),
        "playwright": _command_output(("npx", "playwright", "--version"), cwd=FRONTEND),
    }


def _files_digest(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for relative in paths:
        digest.update(str(relative).encode())
        digest.update(b"\0")
        digest.update((ROOT / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _git_revision() -> str:
    return _command_output(("git", "rev-parse", "HEAD"), cwd=ROOT).lower()


def _command_output(command: tuple[str, ...], *, cwd: Path = ROOT) -> str:
    return subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
