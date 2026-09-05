#!/usr/bin/env python3
"""Verify the public TURN transport matrix under Hub-issued evidence."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import platform
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Mapping
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
from agent.repositories.evidence_identity import (  # noqa: E402
    SqlEvidenceIdentityRepository,
)
from agent.services.hub_evidence_gate_service import (  # noqa: E402
    EvidenceGateRequest,
    EvidenceGateSourceAdmission,
    HubEvidenceGateService,
    canonical_evidence_digest,
)
from agent.services.hub_evidence_registry_service import (  # noqa: E402
    HubEvidenceRegistryService,
)
from scripts.run_hub_evidence_sfu_turn_gate import (  # noqa: E402
    browser_environment,
)

TASK_ID = "SFB-PUBLIC-TURN-EXTERNAL-GATE"
HARNESS = ROOT / "scripts/e2e/public_turn_relay_probe.mjs"
SOURCE_PATHS = (
    ".github/workflows/public-turn-external-evidence.yml",
    "agent/services/hub_evidence_gate_service.py",
    "agent/services/hub_evidence_registry_service.py",
    "ananta_contracts/hub_evidence.py",
    "frontend-angular/package-lock.json",
    "frontend-angular/package.json",
    "scripts/e2e/public_turn_relay_probe.mjs",
    "scripts/e2e/public_turn_relay_probe.test.mjs",
    "scripts/run_hub_evidence_public_turn_gate.py",
    "scripts/run_hub_evidence_sfu_turn_gate.py",
)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_ENGINES = frozenset({"chromium", "firefox"})
_EXPECTED_TRANSPORTS = frozenset({"udp", "tcp", "tls"})


class PublicTurnEvidenceGateError(ValueError):
    """Bounded public-network gate failure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest(root: Path = ROOT) -> dict[str, Any]:
    entries = [
        {
            "path": value,
            "sha256": sha256_file((root / value).resolve(strict=True)),
        }
        for value in SOURCE_PATHS
    ]
    return {"entries": entries, "digest": canonical_evidence_digest(entries)}


def repository_revision(root: Path = ROOT) -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    revision = completed.stdout.strip().lower()
    if completed.returncode != 0 or _SHA.fullmatch(revision) is None:
        raise PublicTurnEvidenceGateError(
            "public_turn_gate_repository_revision_invalid"
        )
    changed = subprocess.run(
        ("git", "diff", "--quiet", "HEAD", "--", *SOURCE_PATHS),
        cwd=root,
        check=False,
    )
    if changed.returncode != 0:
        raise PublicTurnEvidenceGateError("public_turn_gate_bound_sources_dirty")
    return revision


def read_turn_secret() -> str:
    if sys.stdin.isatty():
        raise PublicTurnEvidenceGateError("public_turn_gate_secret_stdin_required")
    secret = sys.stdin.read(4097).strip()
    if (
        not secret
        or len(secret) > 4096
        or any(
            character.isspace() or not character.isprintable()
            for character in secret
        )
    ):
        raise PublicTurnEvidenceGateError("public_turn_gate_secret_invalid")
    return secret


def public_endpoint_environment(host: str) -> dict[str, Any]:
    try:
        addresses = sorted(
            {
                ipaddress.ip_address(sockaddr[0]).compressed
                for family, _, _, _, sockaddr in socket.getaddrinfo(
                    host, 5349, type=socket.SOCK_STREAM
                )
                if family in {socket.AF_INET, socket.AF_INET6}
            }
        )
    except (OSError, ValueError) as exc:
        raise PublicTurnEvidenceGateError(
            "public_turn_gate_dns_resolution_failed"
        ) from exc
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise PublicTurnEvidenceGateError("public_turn_gate_public_address_required")
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 5349), timeout=10) as connection:
            with context.wrap_socket(
                connection, server_hostname=host
            ) as secured:
                certificate = secured.getpeercert(binary_form=True)
                protocol = secured.version()
                cipher = secured.cipher()
    except (OSError, ssl.SSLError) as exc:
        raise PublicTurnEvidenceGateError(
            "public_turn_gate_tls_validation_failed"
        ) from exc
    if not certificate:
        raise PublicTurnEvidenceGateError(
            "public_turn_gate_tls_validation_failed"
        )
    return {
        "schema": "ananta.public-turn-environment.v1",
        "host": host,
        "addresses": addresses,
        "tls_port": 5349,
        "tls_protocol": protocol,
        "tls_cipher": cipher[0] if cipher else None,
        "tls_certificate_sha256": hashlib.sha256(certificate).hexdigest(),
        "platform": platform.system().lower(),
        "machine": platform.machine().lower(),
        "python": platform.python_version(),
        "browsers": browser_environment(),
    }


def project_probe_report(report: Mapping[str, Any]) -> dict[str, Any]:
    rows = report.get("results")
    if not isinstance(rows, list):
        raise PublicTurnEvidenceGateError("public_turn_gate_report_invalid")
    projected = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise PublicTurnEvidenceGateError("public_turn_gate_report_invalid")
        projected.append(
            {
                key: row.get(key)
                for key in (
                    "engine",
                    "transport",
                    "connected",
                    "senderIceState",
                    "receiverIceState",
                    "localCandidateType",
                    "remoteCandidateType",
                    "protocol",
                    "relayProtocol",
                    "pairState",
                    "bytesSent",
                    "bytesReceived",
                    "applicationBytesSent",
                    "applicationBytesReceived",
                    "applicationBytesEchoed",
                )
            }
        )
    return {
        "schema": report.get("schema"),
        "status": report.get("status"),
        "reason_code": report.get("reason_code"),
        "public_host": report.get("public_host"),
        "credential_ttl_seconds": report.get("credential_ttl_seconds"),
        "engines": list(report.get("engines") or []),
        "transports": list(report.get("transports") or []),
        "results": projected,
        "human_intervention_required": report.get(
            "human_intervention_required"
        ),
        "production_capacity": report.get("production_capacity"),
    }


def failed_probe_projection(stderr: str) -> dict[str, Any]:
    reason_code = "public_turn_probe_execution_failed"
    for line in reversed(str(stderr or "").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidate = str(value.get("reason_code") or "") if isinstance(value, Mapping) else ""
        if re.fullmatch(r"public_turn_probe_[a-z0-9_]{1,160}", candidate):
            reason_code = candidate
            break
    return {
        "schema": "ananta.public-turn-relay-probe.v1",
        "status": "failed",
        "reason_code": reason_code,
        "public_host": None,
        "credential_ttl_seconds": 600,
        "engines": [],
        "transports": [],
        "results": [],
        "human_intervention_required": False,
        "production_capacity": False,
    }


def projection_passed(projection: Mapping[str, Any], *, host: str) -> bool:
    rows = projection.get("results")
    if not isinstance(rows, list):
        return False
    combinations = {
        (str(row.get("engine")), str(row.get("transport")))
        for row in rows
        if isinstance(row, Mapping)
    }
    expected = {
        (engine, transport)
        for engine in _EXPECTED_ENGINES
        for transport in _EXPECTED_TRANSPORTS
    }
    return bool(
        projection.get("schema") == "ananta.public-turn-relay-probe.v1"
        and projection.get("status") == "passed"
        and projection.get("public_host") == host
        and projection.get("credential_ttl_seconds") == 600
        and frozenset(projection.get("engines") or ()) == _EXPECTED_ENGINES
        and frozenset(projection.get("transports") or ())
        == _EXPECTED_TRANSPORTS
        and combinations == expected
        and len(rows) == len(expected)
        and all(
            row.get("connected") is True
            and row.get("senderIceState") == "connected"
            and row.get("receiverIceState") == "connected"
            and row.get("localCandidateType") == "relay"
            and row.get("pairState") == "succeeded"
            and int(row.get("bytesSent") or 0) > 0
            and int(row.get("bytesReceived") or 0) > 0
            and int(row.get("applicationBytesReceived") or 0)
            >= int(row.get("applicationBytesSent") or 0)
            > 0
            and int(row.get("applicationBytesEchoed") or 0)
            >= int(row.get("applicationBytesSent") or 0)
            for row in rows
        )
        and projection.get("human_intervention_required") is False
        and projection.get("production_capacity") is False
    )


def _prepare_database(database_url: str) -> str:
    parsed = make_url(database_url)
    if parsed.drivername.startswith("sqlite") and parsed.database not in {
        None,
        "",
        ":memory:",
    }:
        Path(parsed.database).expanduser().resolve().parent.mkdir(
            parents=True, exist_ok=True
        )
    return database_url


def execute_gate(
    *,
    secret: str,
    host: str,
    output_path: Path,
    database_url: str,
    timeout_seconds: int,
) -> tuple[dict[str, Any], int]:
    if re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host) is None:
        raise PublicTurnEvidenceGateError("public_turn_gate_host_invalid")
    if not 30 <= timeout_seconds <= 900:
        raise PublicTurnEvidenceGateError("public_turn_gate_timeout_invalid")
    revision = repository_revision()
    manifest = source_manifest()
    environment = public_endpoint_environment(host)
    execution_profile = {
        "schema": "ananta.public-turn-gate-profile.v1",
        "host": host,
        "engines": sorted(_EXPECTED_ENGINES),
        "transports": sorted(_EXPECTED_TRANSPORTS),
        "ice_transport_policy": "relay",
        "data_channel_echo": True,
        "timeout_seconds": timeout_seconds,
    }
    nonce = uuid.uuid4().hex
    engine = create_engine(_prepare_database(database_url))
    SQLModel.metadata.create_all(
        engine,
        tables=[
            HubSourceEvidenceIdentityDB.__table__,
            HubRunEvidenceIdentityDB.__table__,
        ],
    )
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine))
    endpoint_digest = canonical_evidence_digest(
        {
            "host": host,
            "addresses": environment["addresses"],
            "tls_certificate_sha256": environment[
                "tls_certificate_sha256"
            ],
        }
    )
    policy_digest = canonical_evidence_digest(
        {
            "credential": "turn_rest_hmac_sha1_600_seconds",
            "ice_transport_policy": "relay",
            "transports": sorted(_EXPECTED_TRANSPORTS),
            "secret_output": "forbidden",
        }
    )
    request = EvidenceGateRequest(
        tenant_id="ananta-external",
        project_id="sfu-public-turn",
        task_id=TASK_ID,
        assignment_id=f"public-turn-assignment-{nonce}",
        dispatch_lease_id=f"public-turn-lease-{nonce}",
        repository_revision=revision,
        input_digest=canonical_evidence_digest(
            {
                "repository": manifest["digest"],
                "endpoint": endpoint_digest,
            }
        ),
        execution_profile_digest=canonical_evidence_digest(execution_profile),
        environment_digest=canonical_evidence_digest(environment),
        evidence_scope="external",
        required_scope="external",
        idempotency_key=f"public-turn:{revision}:{endpoint_digest}:{nonce}",
        sources=(
            EvidenceGateSourceAdmission(
                "repository_bundle",
                manifest["digest"],
                manifest["digest"],
                policy_digest,
            ),
            EvidenceGateSourceAdmission(
                "public_turn_endpoint",
                canonical_evidence_digest({"host": host}),
                endpoint_digest,
                policy_digest,
            ),
        ),
    )

    def worker(assignment: Mapping[str, Any]) -> Mapping[str, Any]:
        with tempfile.TemporaryDirectory(
            prefix="ananta-public-turn-gate-"
        ) as temporary:
            raw_report = Path(temporary) / "probe.json"
            completed = subprocess.run(
                (
                    "node",
                    str(HARNESS),
                    "--output",
                    str(raw_report),
                    "--host",
                    host,
                    "--timeout-ms",
                    str(timeout_seconds * 1000),
                ),
                cwd=ROOT,
                input=secret,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds * 8,
            )
            if raw_report.is_file():
                try:
                    loaded = json.loads(raw_report.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise PublicTurnEvidenceGateError(
                        "public_turn_gate_report_invalid"
                    ) from exc
                if not isinstance(loaded, Mapping):
                    raise PublicTurnEvidenceGateError(
                        "public_turn_gate_report_invalid"
                    )
                projection = project_probe_report(loaded)
            else:
                projection = failed_probe_projection(completed.stderr)
                projection["public_host"] = host
            assignment_bound = bool(
                assignment.get("task_id") == TASK_ID
                and assignment.get("evidence_scope") == "external"
                and len(assignment.get("source_ids") or ()) == 2
                and _DIGEST.fullmatch(
                    str(assignment.get("binding_digest") or "")
                )
                is not None
            )
            secret_exposed = secret in json.dumps(projection, sort_keys=True)
            passed = bool(
                completed.returncode == 0
                and assignment_bound
                and not secret_exposed
                and projection_passed(projection, host=host)
            )
            return {
                "passed": passed,
                "reason_code": (
                    "public_turn_external_gate_passed"
                    if passed
                    else "public_turn_external_gate_failed"
                ),
                "assignment_bound": assignment_bound,
                "secret_exposed": secret_exposed,
                "probe": projection,
                "stdout_digest": hashlib.sha256(
                    completed.stdout.encode()
                ).hexdigest(),
                "stderr_digest": hashlib.sha256(
                    completed.stderr.encode()
                ).hexdigest(),
            }

    outcome = HubEvidenceGateService(registry).execute(request, worker)
    report = {
        "schema": "ananta.hub-evidence-public-turn-gate-result.v1",
        "status": "passed" if outcome.passed and outcome.verified else "failed",
        "reason_code": outcome.reason_code,
        "repository_revision": revision,
        "source_ids": list(outcome.source_ids),
        "run_id": outcome.run_id,
        "result_digest": outcome.result_digest,
        "evidence_scope": "external",
        "verified": outcome.verified,
        "execution_profile": execution_profile,
        "environment": environment,
        "execution": dict(outcome.execution),
        "human_intervention_required": False,
        "production_release_eligible": False,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if secret in encoded:
        raise PublicTurnEvidenceGateError("public_turn_gate_secret_exposed")
    output_path.resolve().parent.mkdir(parents=True, exist_ok=True)
    output_path.resolve().write_text(encoded, encoding="utf-8")
    return report, 0 if report["status"] == "passed" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="webrtc.ananta.de")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/public-turn-external-evidence.json",
    )
    parser.add_argument(
        "--database-url",
        default=f"sqlite:///{ROOT / 'data/hub-evidence-public-turn.sqlite3'}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report, returncode = execute_gate(
        secret=read_turn_secret(),
        host=args.host,
        output_path=args.output,
        database_url=args.database_url,
        timeout_seconds=args.timeout_seconds,
    )
    print(
        json.dumps(
            {"status": report["status"], "run_id": report["run_id"]},
            sort_keys=True,
        )
    )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
