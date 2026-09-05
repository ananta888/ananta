#!/usr/bin/env python3
"""Run the real local LiveKit/coturn/browser path under Hub-issued evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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
from scripts.e2e.sfu_broadcast_local_turn_relay_e2e import (  # noqa: E402
    LIVEKIT_REPO_DIGEST,
    TURN_REPO_DIGEST,
)
from scripts.e2e.sfu_broadcast_local_turn_relay_e2e import (  # noqa: E402
    execute as execute_relay_harness,
)

TASK_ID = "SFB-TURN-LOCAL-HUB-EVIDENCE"
SOURCE_PATHS = (
    "agent/services/hub_evidence_gate_service.py",
    "agent/services/hub_evidence_registry_service.py",
    "ananta_contracts/hub_evidence.py",
    "config/livekit.semantic-media.yaml",
    "docker-compose.semantic-media.yml",
    "frontend-angular/package-lock.json",
    "frontend-angular/package.json",
    "scripts/e2e/sfu_broadcast_local_turn_relay_e2e.py",
    "scripts/run_hub_evidence_sfu_turn_gate.py",
    "scripts/spikes/semantic_sfu_three_peer.mjs",
)
class SfuTurnEvidenceGateError(ValueError):
    """Bounded gate configuration or environment failure."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest(root: Path = ROOT) -> dict[str, Any]:
    entries = [
        {"path": value, "sha256": sha256_file((root / value).resolve(strict=True))}
        for value in SOURCE_PATHS
    ]
    return {"entries": entries, "digest": canonical_evidence_digest(entries)}


def repository_image_digest(reference: str) -> str:
    value = str(reference).rsplit("@sha256:", 1)[-1]
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SfuTurnEvidenceGateError("sfu_turn_gate_image_reference_invalid")
    return value


def repository_revision(root: Path = ROOT) -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=root, capture_output=True, text=True, check=False
    )
    revision = completed.stdout.strip().lower()
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise SfuTurnEvidenceGateError("sfu_turn_gate_repository_revision_invalid")
    changed = subprocess.run(
        ("git", "diff", "--quiet", "HEAD", "--", *SOURCE_PATHS), cwd=root, check=False
    )
    if changed.returncode != 0:
        raise SfuTurnEvidenceGateError("sfu_turn_gate_bound_sources_dirty")
    return revision


def browser_environment(root: Path = ROOT) -> list[dict[str, str]]:
    script = """
import { chromium, firefox } from 'playwright';
import { execFileSync } from 'node:child_process';
for (const engine of [chromium, firefox]) {
  const executable = engine.executablePath();
  const version = execFileSync(executable, ['--version'], { encoding: 'utf8' }).trim();
  console.log(JSON.stringify({ engine: engine.name(), executable, version }));
}
"""
    completed = subprocess.run(
        ("node", "--input-type=module", "-e", script),
        cwd=root / "frontend-angular",
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise SfuTurnEvidenceGateError("sfu_turn_gate_browser_runtime_unavailable")
    rows: list[dict[str, str]] = []
    try:
        for line in completed.stdout.splitlines():
            value = json.loads(line)
            executable = Path(str(value["executable"])).resolve(strict=True)
            rows.append(
                {
                    "engine": str(value["engine"]),
                    "version": str(value["version"])[:160],
                    "executable_sha256": sha256_file(executable),
                }
            )
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        raise SfuTurnEvidenceGateError("sfu_turn_gate_browser_runtime_invalid") from exc
    if {row["engine"] for row in rows} != {"chromium", "firefox"}:
        raise SfuTurnEvidenceGateError("sfu_turn_gate_browser_runtime_invalid")
    return rows


def project_relay_result(result: Mapping[str, Any]) -> dict[str, Any]:
    source = result.get("source_report")
    if not isinstance(source, Mapping):
        raise SfuTurnEvidenceGateError("sfu_turn_gate_source_report_invalid")
    engine_rows = source.get("engines")
    if not isinstance(engine_rows, list):
        raise SfuTurnEvidenceGateError("sfu_turn_gate_engine_report_invalid")
    projected_engines: list[dict[str, Any]] = []
    for row in engine_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("peers"), list):
            raise SfuTurnEvidenceGateError("sfu_turn_gate_engine_report_invalid")
        peers = [peer for peer in row["peers"] if isinstance(peer, Mapping)]
        receivers = [peer for peer in peers if str(peer.get("identity") or "").startswith("receiver-")]
        wrong_key = next((peer for peer in peers if peer.get("identity") == "wrong-key-probe"), {})
        publisher = next((peer for peer in peers if peer.get("identity") == "publisher"), {})
        projected_engines.append(
            {
                "engine": row.get("engine"),
                "relay_required": row.get("relay_required"),
                "relay_selected": row.get("relay_selected"),
                "publisher_outbound_video_bytes": publisher.get("outbound_video_bytes"),
                "receiver_inbound_video_bytes": [peer.get("inbound_video_bytes") for peer in receivers],
                "receiver_decoded_samples": [peer.get("decoded_samples") for peer in receivers],
                "wrong_key_inbound_video_bytes": wrong_key.get("inbound_video_bytes"),
                "wrong_key_decoded_samples": wrong_key.get("decoded_samples"),
            }
        )
    return {
        "status": result.get("status"),
        "claims": dict(result.get("claims") or {}),
        "pinned_images": dict(result.get("pinned_images") or {}),
        "container_image_ids": dict(result.get("container_image_ids") or {}),
        "cleanup": dict(result.get("cleanup") or {}),
        "topology": dict(source.get("topology") or {}),
        "e2ee": {
            "enabled": dict(source.get("e2ee") or {}).get("enabled"),
            "server_plaintext_access": dict(source.get("e2ee") or {}).get(
                "server_plaintext_access"
            ),
        },
        "transport_profile": source.get("transport_profile"),
        "engines": projected_engines,
    }


def projection_passed(projection: Mapping[str, Any], *, receiver_count: int) -> bool:
    claims = dict(projection.get("claims") or {})
    cleanup = dict(projection.get("cleanup") or {})
    topology = dict(projection.get("topology") or {})
    e2ee = dict(projection.get("e2ee") or {})
    engines = list(projection.get("engines") or [])
    pinned_images = dict(projection.get("pinned_images") or {})
    container_image_ids = dict(projection.get("container_image_ids") or {})
    return bool(
        projection.get("status") == "passed"
        and projection.get("transport_profile") == "turn_relay_required"
        and claims.get("real_browser_contexts") is True
        and claims.get("real_livekit_process") is True
        and claims.get("real_coturn_relay_selected") is True
        and claims.get("wrong_key_media_not_decoded") is True
        and claims.get("production_capacity") is False
        and pinned_images == {"livekit": LIVEKIT_REPO_DIGEST, "coturn": TURN_REPO_DIGEST}
        and container_image_ids
        == {
            "livekit": f"sha256:{repository_image_digest(LIVEKIT_REPO_DIGEST)}",
            "coturn": f"sha256:{repository_image_digest(TURN_REPO_DIGEST)}",
        }
        and cleanup == {"compose_project_removed": True, "host_turn_container_removed": True}
        and topology.get("publishers") == 1
        and topology.get("receivers") == receiver_count
        and e2ee.get("enabled") is True
        and e2ee.get("server_plaintext_access") is False
        and {row.get("engine") for row in engines} == {"chromium", "firefox"}
        and all(
            row.get("relay_required") is True
            and row.get("relay_selected") is True
            and len(row.get("receiver_inbound_video_bytes") or []) == receiver_count
            and all(int(value or 0) > 0 for value in row.get("receiver_inbound_video_bytes") or [])
            and all(int(value or 0) >= 3 for value in row.get("receiver_decoded_samples") or [])
            and int(row.get("wrong_key_inbound_video_bytes") or 0) > 0
            and row.get("wrong_key_decoded_samples") == 0
            for row in engines
        )
    )


def execute_gate(
    *,
    output_path: Path,
    database_url: str,
    receiver_count: int = 3,
) -> tuple[dict[str, Any], int]:
    if not 3 <= receiver_count <= 7:
        raise SfuTurnEvidenceGateError("sfu_turn_gate_receiver_count_invalid")
    revision = repository_revision()
    manifest = source_manifest()
    browsers = browser_environment()
    environment = {
        "schema": "ananta.sfu-turn-gate-environment.v1",
        "host": platform.node(),
        "machine": platform.machine().lower(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "browsers": browsers,
    }
    profile = {
        "schema": "ananta.sfu-turn-gate-profile.v1",
        "receiver_count": receiver_count,
        "engines": ["chromium", "firefox"],
        "livekit_image": LIVEKIT_REPO_DIGEST,
        "coturn_image": TURN_REPO_DIGEST,
        "transport_profile": "turn_relay_required",
    }
    image_policy_digest = canonical_evidence_digest(profile)
    nonce = uuid.uuid4().hex
    engine = create_engine(database_url)
    SQLModel.metadata.create_all(
        engine,
        tables=[HubSourceEvidenceIdentityDB.__table__, HubRunEvidenceIdentityDB.__table__],
    )
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine))
    request = EvidenceGateRequest(
        tenant_id="ananta-local",
        project_id="sfu-turn-relay",
        task_id=TASK_ID,
        assignment_id=f"sfu-turn-assignment-{nonce}",
        dispatch_lease_id=f"sfu-turn-lease-{nonce}",
        repository_revision=revision,
        input_digest=canonical_evidence_digest(
            {"repository": manifest["digest"], "profile": profile, "browsers": browsers}
        ),
        execution_profile_digest=canonical_evidence_digest(profile),
        environment_digest=canonical_evidence_digest(environment),
        evidence_scope="local",
        required_scope="local",
        idempotency_key=f"sfu-turn:{revision}:{nonce}",
        sources=(
            EvidenceGateSourceAdmission(
                "repository_bundle", manifest["digest"], manifest["digest"], image_policy_digest
            ),
            EvidenceGateSourceAdmission(
                "livekit_image",
                canonical_evidence_digest(LIVEKIT_REPO_DIGEST),
                repository_image_digest(LIVEKIT_REPO_DIGEST),
                image_policy_digest,
            ),
            EvidenceGateSourceAdmission(
                "coturn_image",
                canonical_evidence_digest(TURN_REPO_DIGEST),
                repository_image_digest(TURN_REPO_DIGEST),
                image_policy_digest,
            ),
            EvidenceGateSourceAdmission(
                "browser_runtime",
                canonical_evidence_digest(browsers),
                canonical_evidence_digest(browsers),
                image_policy_digest,
            ),
        ),
    )

    def worker(assignment: Mapping[str, Any]) -> Mapping[str, Any]:
        with tempfile.TemporaryDirectory(prefix="ananta-sfu-turn-hub-") as temporary:
            result = execute_relay_harness(
                Path(temporary) / "relay.json", receiver_count=receiver_count
            )
        projection = project_relay_result(result)
        immutable_inputs_unchanged = bool(
            source_manifest()["digest"] == manifest["digest"]
            and browser_environment() == browsers
        )
        passed = projection_passed(projection, receiver_count=receiver_count) and immutable_inputs_unchanged
        return {
            "passed": passed,
            "reason_code": "sfu_turn_gate_passed" if passed else "sfu_turn_gate_failed",
            "assignment": {
                "run_id": assignment.get("run_id"),
                "source_ids": list(assignment.get("source_ids") or []),
                "assignment_id": assignment.get("assignment_id"),
                "dispatch_lease_id": assignment.get("dispatch_lease_id"),
            },
            "projection": projection,
            "immutable_inputs_unchanged": immutable_inputs_unchanged,
        }

    outcome = HubEvidenceGateService(registry).execute(request, worker)
    report = {
        "schema": "ananta.hub-evidence-sfu-turn-gate-result.v1",
        "status": "passed" if outcome.passed and outcome.verified else "failed",
        "reason_code": outcome.reason_code,
        "repository_revision": revision,
        "source_ids": list(outcome.source_ids),
        "run_id": outcome.run_id,
        "result_digest": outcome.result_digest,
        "evidence_scope": "local",
        "verified": outcome.verified,
        "execution_profile": profile,
        "environment": environment,
        "execution": dict(outcome.execution),
        "human_intervention_required": False,
        "production_release_eligible": False,
        "public_network_path_verified": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report, 0 if report["status"] == "passed" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver-count", type=int, default=3)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/sfu-turn-hub-evidence.json"
    )
    parser.add_argument(
        "--database-url", default=f"sqlite:///{ROOT / 'data/hub-evidence-sfu-turn.sqlite3'}"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report, returncode = execute_gate(
        output_path=args.output,
        database_url=args.database_url,
        receiver_count=args.receiver_count,
    )
    print(json.dumps({"status": report["status"], "run_id": report["run_id"]}, sort_keys=True))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
