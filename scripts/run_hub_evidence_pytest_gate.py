#!/usr/bin/env python3
"""Run a fixed pytest profile under a Hub-issued evidence reservation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url
from sqlmodel import SQLModel, create_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.db_models.evidence_identity import (  # noqa: E402
    HubRunEvidenceIdentityDB,
    HubSourceEvidenceIdentityDB,
)
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository  # noqa: E402
from agent.services.hub_evidence_gate_service import (  # noqa: E402
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    HubEvidenceGateService,
    canonical_evidence_digest,
)
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService  # noqa: E402

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,95}$")
_TRUSTED_RUNNER_PATHS = (
    "agent/services/hub_evidence_gate_service.py",
    "agent/services/hub_evidence_registry_service.py",
    "ananta_contracts/hub_evidence.py",
    "scripts/run_hub_evidence_pytest_gate.py",
)
_FORBIDDEN_PYTEST_OPTIONS = frozenset(
    {"-c", "--basetemp", "--confcutdir", "--junitxml", "--override-ini", "--pyargs", "--rootdir"}
)
_SECRET_ENV_MARKERS = ("API_KEY", "AUTH", "CREDENTIAL", "PASSWORD", "SECRET", "TOKEN")
_ALLOWED_FIELDS = frozenset(
    {
        "schema",
        "profile_id",
        "task_id",
        "tenant_id",
        "project_id",
        "evidence_scope",
        "required_scope",
        "timeout_seconds",
        "source_paths",
        "pytest_args",
        "environment",
        "minimum_tests",
        "forbid_skips",
    }
)


class HubEvidencePytestGateError(ValueError):
    pass


def load_profile(path: Path, *, root: Path = ROOT) -> dict[str, Any]:
    resolved = _within_root(path, root=root, require_file=True)
    profile = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(profile, dict) or set(profile) != _ALLOWED_FIELDS:
        raise HubEvidencePytestGateError("hub_evidence_gate_profile_fields_invalid")
    if (
        profile.get("schema") != "ananta.hub-evidence-pytest-gate.v1"
        or not _SAFE_ID.fullmatch(str(profile.get("profile_id") or ""))
        or not _SAFE_ID.fullmatch(str(profile.get("task_id") or ""))
        or not _SAFE_ID.fullmatch(str(profile.get("tenant_id") or ""))
        or not _SAFE_ID.fullmatch(str(profile.get("project_id") or ""))
        or profile.get("evidence_scope") not in {"local", "external", "production"}
        or profile.get("required_scope") not in {"local", "external", "production"}
        or profile.get("evidence_scope") != profile.get("required_scope")
    ):
        raise HubEvidencePytestGateError("hub_evidence_gate_profile_identity_invalid")
    timeout = profile.get("timeout_seconds")
    minimum = profile.get("minimum_tests")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not 30 <= timeout <= 7_200
        or isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or minimum < 1
        or not isinstance(profile.get("forbid_skips"), bool)
    ):
        raise HubEvidencePytestGateError("hub_evidence_gate_profile_limits_invalid")
    sources = profile.get("source_paths")
    if not isinstance(sources, list) or not sources or len(sources) > 256:
        raise HubEvidencePytestGateError("hub_evidence_gate_source_paths_invalid")
    normalized_sources = [_relative_source(value, root=root) for value in sources]
    if len(set(normalized_sources)) != len(normalized_sources):
        raise HubEvidencePytestGateError("hub_evidence_gate_source_paths_invalid")
    arguments = profile.get("pytest_args")
    if (
        not isinstance(arguments, list)
        or not arguments
        or len(arguments) > 512
        or any(not isinstance(value, str) or not value or "\x00" in value for value in arguments)
        or any(value.split("=", 1)[0] in _FORBIDDEN_PYTEST_OPTIONS for value in arguments)
        or any(value.startswith("/") or ".." in Path(value).parts for value in arguments)
    ):
        raise HubEvidencePytestGateError("hub_evidence_gate_pytest_args_invalid")
    environment = profile.get("environment")
    if not isinstance(environment, dict) or any(
        not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", str(key))
        or any(marker in str(key) for marker in _SECRET_ENV_MARKERS)
        or not isinstance(value, str)
        or len(value) > 256
        for key, value in environment.items()
    ):
        raise HubEvidencePytestGateError("hub_evidence_gate_environment_invalid")
    return {**profile, "source_paths": normalized_sources}


def run_profile(
    profile: Mapping[str, Any],
    *,
    profile_path: Path,
    output_path: Path,
    database_url: str,
    junit_path: Path,
    root: Path = ROOT,
    require_clean: bool = True,
) -> tuple[dict[str, Any], int]:
    revision = _repository_revision(root, require_clean=require_clean)
    source_manifest = _source_manifest(profile["source_paths"], root=root)
    profile_digest = hashlib.sha256(profile_path.resolve().read_bytes()).hexdigest()
    environment = _execution_environment(profile)
    environment_digest = canonical_evidence_digest(environment)
    execution_profile_digest = canonical_evidence_digest(
        {"pytest_args": profile["pytest_args"], "environment": profile["environment"]}
    )
    input_digest = canonical_evidence_digest(
        {
            "profile_digest": profile_digest,
            "source_manifest_digest": source_manifest["digest"],
            "repository_revision": revision,
        }
    )
    nonce = uuid.uuid4().hex
    database = create_engine(_prepare_database(database_url))
    SQLModel.metadata.create_all(
        database,
        tables=[HubSourceEvidenceIdentityDB.__table__, HubRunEvidenceIdentityDB.__table__],
    )
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(database))
    request = EvidenceGateRequest(
        tenant_id=str(profile["tenant_id"]),
        project_id=str(profile["project_id"]),
        task_id=str(profile["task_id"]),
        assignment_id=f"gate-assignment-{nonce}",
        dispatch_lease_id=f"gate-lease-{nonce}",
        repository_revision=revision,
        input_digest=input_digest,
        execution_profile_digest=execution_profile_digest,
        environment_digest=environment_digest,
        evidence_scope=profile["evidence_scope"],
        required_scope=profile["required_scope"],
        idempotency_key=f"{profile['profile_id']}:{revision}:{nonce}",
        sources=(
            EvidenceGateSourceAdmission(
                origin_type="repository_bundle",
                origin_digest=profile_digest,
                content_digest=source_manifest["digest"],
                policy_digest=profile_digest,
            ),
        ),
    )
    junit = junit_path.resolve()
    junit.parent.mkdir(parents=True, exist_ok=True)

    def execute(assignment: Mapping[str, Any]) -> Mapping[str, Any]:
        command = [sys.executable, "-m", "pytest", *profile["pytest_args"], f"--junitxml={junit}"]
        child_environment = dict(os.environ)
        child_environment.update(profile["environment"])
        child_environment["ANANTA_HUB_EVIDENCE_ASSIGNMENT"] = json.dumps(
            assignment, sort_keys=True, separators=(",", ":")
        )
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                env=child_environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=int(profile["timeout_seconds"]),
            )
            sys.stdout.write(completed.stdout)
            sys.stderr.write(completed.stderr)
            counts = _junit_counts(junit)
            passed = (
                completed.returncode == 0
                and counts["tests"] >= int(profile["minimum_tests"])
                and counts["failures"] == 0
                and counts["errors"] == 0
                and (not profile["forbid_skips"] or counts["skipped"] == 0)
            )
            return {
                "passed": passed,
                "reason_code": "hub_evidence_pytest_gate_passed" if passed else "hub_evidence_pytest_gate_failed",
                "returncode": completed.returncode,
                "counts": counts,
                "stdout_digest": hashlib.sha256(completed.stdout.encode()).hexdigest(),
                "stderr_digest": hashlib.sha256(completed.stderr.encode()).hexdigest(),
                "junit_digest": hashlib.sha256(junit.read_bytes()).hexdigest(),
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "passed": False,
                "reason_code": "hub_evidence_pytest_gate_timeout",
                "returncode": -1,
                "counts": {"tests": 0, "failures": 0, "errors": 0, "skipped": 0},
                "stdout_digest": _timeout_digest(exc.stdout),
                "stderr_digest": _timeout_digest(exc.stderr),
            }

    outcome = HubEvidenceGateService(registry).execute(request, execute)
    report = {
        "schema": "ananta.hub-evidence-pytest-gate-result.v1",
        "profile_id": profile["profile_id"],
        "task_id": profile["task_id"],
        "repository_revision": revision,
        "profile_digest": profile_digest,
        "source_manifest_digest": source_manifest["digest"],
        "source_ids": list(outcome.source_ids),
        "run_id": outcome.run_id,
        "result_digest": outcome.result_digest,
        "evidence_scope": profile["evidence_scope"],
        "passed": outcome.passed,
        "verified": outcome.verified,
        "reason_code": outcome.reason_code,
        "execution": dict(outcome.execution),
        "human_intervention_required": False,
    }
    output = output_path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report, 0 if outcome.passed and outcome.verified else 1


def _source_manifest(paths: Sequence[str], *, root: Path) -> dict[str, Any]:
    bound_paths = tuple(dict.fromkeys([*paths, *_TRUSTED_RUNNER_PATHS]))
    entries = [
        {
            "path": path,
            "sha256": hashlib.sha256(_within_root(root / path, root=root, require_file=True).read_bytes()).hexdigest(),
        }
        for path in bound_paths
    ]
    return {"entries": entries, "digest": canonical_evidence_digest(entries)}


def _execution_environment(profile: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": "ananta.hub-evidence-pytest-environment.v1",
        "python": platform.python_version(),
        "platform": platform.system().lower(),
        "machine": platform.machine().lower(),
        "ci": os.environ.get("CI") == "true",
        "profile_environment": dict(sorted(profile["environment"].items())),
    }


def _junit_counts(path: Path) -> dict[str, int]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise HubEvidencePytestGateError("hub_evidence_gate_junit_invalid") from exc
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise HubEvidencePytestGateError("hub_evidence_gate_junit_invalid")
    return {
        field: sum(int(suite.attrib.get(field, "0")) for suite in suites)
        for field in ("tests", "failures", "errors", "skipped")
    }


def _repository_revision(root: Path, *, require_clean: bool) -> str:
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False)
    value = revision.stdout.strip().lower()
    if revision.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise HubEvidencePytestGateError("hub_evidence_gate_revision_unavailable")
    if require_clean:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0 or status.stdout.strip():
            raise HubEvidencePytestGateError("hub_evidence_gate_repository_dirty")
    return value


def _relative_source(value: Any, *, root: Path) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise HubEvidencePytestGateError("hub_evidence_gate_source_paths_invalid")
    path = _within_root(root / value, root=root, require_file=True)
    return path.relative_to(root.resolve()).as_posix()


def _within_root(path: Path, *, root: Path, require_file: bool) -> Path:
    resolved_root = root.resolve()
    try:
        resolved = path.resolve(strict=require_file)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise HubEvidencePytestGateError("hub_evidence_gate_path_invalid") from exc
    if require_file and not resolved.is_file():
        raise HubEvidencePytestGateError("hub_evidence_gate_path_invalid")
    return resolved


def _timeout_digest(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        payload = value
    else:
        payload = str(value or "").encode()
    return hashlib.sha256(payload).hexdigest()


def _prepare_database(database_url: str) -> str:
    parsed = make_url(database_url)
    if parsed.drivername.startswith("sqlite") and parsed.database not in {None, "", ":memory:"}:
        Path(parsed.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    return database_url


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument(
        "--database-url",
        default=f"sqlite:///{ROOT / 'data' / 'hub-evidence-gates.sqlite3'}",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    profile_path = _within_root(args.profile, root=ROOT, require_file=True)
    profile = load_profile(profile_path)
    report, returncode = run_profile(
        profile,
        profile_path=profile_path,
        output_path=args.output,
        database_url=args.database_url,
        junit_path=args.junit,
    )
    print(
        f"hub evidence gate {report['profile_id']}: "
        f"passed={report['passed']} verified={report['verified']} "
        f"run_id={report['run_id']}"
    )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
