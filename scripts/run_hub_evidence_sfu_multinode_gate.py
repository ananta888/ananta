#!/usr/bin/env python3
"""Run the real local two-node SFU gate under Hub-issued evidence identities."""

from __future__ import annotations

import argparse
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
from scripts.e2e.sfu_broadcast_local_multinode_e2e import (  # noqa: E402
    LIVEKIT_IMAGE,
    REDIS_IMAGE,
    media_passed,
)
from scripts.e2e.sfu_broadcast_local_multinode_e2e import (  # noqa: E402
    execute as execute_multinode_harness,
)
from scripts.e2e.sfu_broadcast_local_turn_relay_e2e import TURN_REPO_DIGEST  # noqa: E402
from scripts.run_hub_evidence_sfu_turn_gate import (  # noqa: E402
    browser_environment,
    repository_image_digest,
    sha256_file,
)

TASK_ID = "SFB-OPS-016"
SOURCE_PATHS = (
    "agent/services/hub_evidence_gate_service.py",
    "agent/services/hub_evidence_registry_service.py",
    "ananta_contracts/hub_evidence.py",
    "config/livekit.sfu-broadcast-native.yaml",
    "config/redis/sfu-broadcast.conf",
    "config/sfu_broadcast_cluster_directory.json",
    "docker-compose.sfu-broadcast.yml",
    "frontend-angular/package-lock.json",
    "frontend-angular/package.json",
    "scripts/e2e/sfu_broadcast_local_multinode_e2e.py",
    "scripts/e2e/sfu_broadcast_local_turn_relay_e2e.py",
    "scripts/run_hub_evidence_sfu_multinode_gate.py",
    "scripts/spikes/semantic_sfu_three_peer.mjs",
)


class SfuMultinodeEvidenceGateError(ValueError):
    """Bounded gate configuration or immutable-input failure."""


def source_manifest(root: Path = ROOT) -> dict[str, Any]:
    entries = [{"path": value, "sha256": sha256_file((root / value).resolve(strict=True))} for value in SOURCE_PATHS]
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
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise SfuMultinodeEvidenceGateError("sfu_multinode_repository_revision_invalid")
    changed = subprocess.run(("git", "diff", "--quiet", "HEAD", "--", *SOURCE_PATHS), cwd=root, check=False)
    if changed.returncode != 0:
        raise SfuMultinodeEvidenceGateError("sfu_multinode_bound_sources_dirty")
    return revision


def projection_passed(projection: Mapping[str, Any]) -> bool:
    claims = dict(projection.get("claims") or {})
    topology = dict(projection.get("topology") or {})
    observations = dict(projection.get("observations") or {})
    cleanup = dict(projection.get("cleanup") or {})
    image_ids = dict(projection.get("container_image_ids") or {})
    return bool(
        projection.get("status") == "passed"
        and projection.get("scope") == "local_single_host"
        and claims
        == {
            "real_livekit_processes": True,
            "real_tls_redis_process": True,
            "real_browser_processes": True,
            "native_placement_owner": "livekit",
            "public_network_path": False,
            "independent_failure_domains": False,
            "production_capacity": False,
        }
        and projection.get("pinned_images")
        == {"livekit": LIVEKIT_IMAGE, "redis": REDIS_IMAGE, "coturn": TURN_REPO_DIGEST}
        and set(image_ids)
        == {"sfu-broadcast-redis", "sfu-broadcast-livekit-native-a", "sfu-broadcast-livekit-native-b", "coturn"}
        and all(re.fullmatch(r"sha256:[0-9a-f]{64}", str(value)) for value in image_ids.values())
        and topology == {"livekit_nodes": 2, "redis_nodes": 1, "host_count": 1}
        and observations.get("initial_registered_nodes") == 2
        and observations.get("drained_registered_nodes") == 1
        and observations.get("rejoined_registered_nodes") == 2
        and isinstance(observations.get("drain_recovery_ms"), int)
        and 0 < observations["drain_recovery_ms"] <= 30_000
        and media_passed(dict(observations.get("after_drain_media") or {}))
        and cleanup
        == {
            "owned_containers_and_volumes_removed": True,
            "owned_turn_container_removed": True,
        }
    )


def execute_gate(*, output_path: Path, database_url: str) -> tuple[dict[str, Any], int]:
    revision = repository_revision()
    manifest = source_manifest()
    browsers = browser_environment()
    environment = {
        "schema": "ananta.sfu-multinode-gate-environment.v1",
        "host": platform.node(),
        "machine": platform.machine().lower(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "browsers": browsers,
    }
    profile = {
        "schema": "ananta.sfu-multinode-gate-profile.v1",
        "livekit_nodes": 2,
        "redis_nodes": 1,
        "host_count": 1,
        "engines": ["chromium", "firefox"],
        "livekit_image": LIVEKIT_IMAGE,
        "redis_image": REDIS_IMAGE,
        "coturn_image": TURN_REPO_DIGEST,
        "required_scope": "local",
    }
    policy_digest = canonical_evidence_digest(profile)
    nonce = uuid.uuid4().hex
    engine = create_engine(database_url)
    SQLModel.metadata.create_all(
        engine,
        tables=[HubSourceEvidenceIdentityDB.__table__, HubRunEvidenceIdentityDB.__table__],
    )
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine))
    images = {
        "livekit_image": LIVEKIT_IMAGE,
        "redis_image": REDIS_IMAGE,
        "coturn_image": TURN_REPO_DIGEST,
    }
    request = EvidenceGateRequest(
        tenant_id="ananta-local",
        project_id="sfu-broadcast-multinode",
        task_id=TASK_ID,
        assignment_id=f"sfu-multinode-assignment-{nonce}",
        dispatch_lease_id=f"sfu-multinode-lease-{nonce}",
        repository_revision=revision,
        input_digest=canonical_evidence_digest(
            {"repository": manifest["digest"], "profile": profile, "browsers": browsers}
        ),
        execution_profile_digest=canonical_evidence_digest(profile),
        environment_digest=canonical_evidence_digest(environment),
        evidence_scope="local",
        required_scope="local",
        idempotency_key=f"sfu-multinode:{revision}:{nonce}",
        sources=(
            EvidenceGateSourceAdmission("repository_bundle", manifest["digest"], manifest["digest"], policy_digest),
            *(
                EvidenceGateSourceAdmission(
                    name,
                    canonical_evidence_digest(reference),
                    repository_image_digest(reference),
                    policy_digest,
                )
                for name, reference in images.items()
            ),
            EvidenceGateSourceAdmission(
                "browser_runtime",
                canonical_evidence_digest(browsers),
                canonical_evidence_digest(browsers),
                policy_digest,
            ),
        ),
    )

    def worker(assignment: Mapping[str, Any]) -> Mapping[str, Any]:
        with tempfile.TemporaryDirectory(prefix="ananta-sfu-multinode-hub-") as temporary:
            projection = execute_multinode_harness(Path(temporary) / "multinode.json")
        immutable_inputs_unchanged = bool(
            source_manifest()["digest"] == manifest["digest"] and browser_environment() == browsers
        )
        passed = projection_passed(projection) and immutable_inputs_unchanged
        return {
            "passed": passed,
            "reason_code": ("sfu_multinode_gate_passed" if passed else "sfu_multinode_gate_failed"),
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
        "schema": "ananta.hub-evidence-sfu-multinode-gate-result.v1",
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
        "independent_failure_domains_verified": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report, 0 if report["status"] == "passed" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/sfu-multinode-hub-evidence.json",
    )
    parser.add_argument(
        "--database-url",
        default=f"sqlite:///{ROOT / 'data/hub-evidence-sfu-multinode.sqlite3'}",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    report, returncode = execute_gate(output_path=arguments.output, database_url=arguments.database_url)
    print(json.dumps({"status": report["status"], "run_id": report["run_id"]}, sort_keys=True))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
