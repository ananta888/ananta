#!/usr/bin/env python3
"""Build the deterministic WebRTC/SFU broadcast source baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_RELATIVE = Path("artifacts/domain/webrtc-sfu-broadcast-baseline.json")
OUTPUT = ROOT / OUTPUT_RELATIVE
TOOL_VERSION = "1.0.0"
CLASSIFICATIONS = frozenset({"present", "partial", "missing"})
CONFIG_PATHS = (
    "config/livekit.semantic-media.yaml",
    "docker-compose.semantic-media.yml",
    "frontend-angular/package.json",
)


class AuditFailure(ValueError):
    """A fail-closed, machine-readable baseline failure."""


@dataclass(frozen=True, slots=True)
class Probe:
    finding: str
    area: str
    layer: str
    status: str
    path: str
    symbol: str | None
    evidence: str
    expected_present: bool = True


PROBES = (
    Probe(
        "angular_livekit_client_dependency", "sfu", "angular_dependency", "present",
        "frontend-angular/package.json", '"livekit-client": "2.20.1"',
        "Angular pins the browser LiveKit client exactly at 2.20.1.",
    ),
    Probe(
        "broadcast_feature_flags", "sfu", "production", "partial",
        "agent/services/sfu_broadcast_feature_policy.py", 'key="semantic_media_broadcast"',
        "Hub-owned broadcast flags exist and remain default-false; activation evidence is still blocked.",
    ),
    Probe(
        "broadcast_group_limit_8", "sfu", "production", "present",
        "agent/services/sfu_broadcast_participant_limits.py", "SFU_BROADCAST_MAX_GROUP_MEMBERS = 8",
        "The current hard room/group participant limit is eight.",
    ),
    Probe(
        "broadcast_publication_receiver_limit_7", "sfu", "production", "present",
        "frontend-angular/src/app/services/sfu-broadcast-limits.ts",
        "SFU_BROADCAST_MAX_PUBLICATION_RECIPIENTS = 7",
        "The current publisher fan-out limit is seven receivers.",
    ),
    Probe(
        "compose_livekit_sfu", "sfu", "active_compose", "present",
        "docker-compose.semantic-media.yml", "semantic-media-sfu:",
        "The active semantic-media Compose file contains a pinned, isolated LiveKit service.",
    ),
    Probe(
        "compose_turn_gate", "turn", "active_compose", "partial",
        "docker-compose.semantic-media.yml", "semantic-media-turn-gate:",
        "A bounded coturn gate exists, but it is not an external multi-region TURN fleet.",
    ),
    Probe(
        "group_key_authorization_schema", "e2ee", "schema", "present",
        "schemas/webrtc/group_key_epoch.v1.json", "Ananta WebRTC Group Key Epoch Authorization v1",
        "The schema carries signed member and key-package references without content-key material.",
    ),
    Probe(
        "hub_sfu_admission", "sfu", "production", "present",
        "agent/services/semantic_sfu_admission_service.py", "SemanticSfuAdmissionService",
        "The Hub owns membership/revision checks and narrowed SFU grants.",
    ),
    Probe(
        "livekit_base_key_provider", "e2ee", "production", "present",
        "frontend-angular/src/app/services/livekit-sfu-room.adapter.ts", "BaseKeyProvider",
        "The productive LiveKit room adapter uses the client SDK BaseKeyProvider path.",
    ),
    Probe(
        "livekit_runtime_config", "sfu", "configuration", "partial",
        "config/livekit.semantic-media.yaml", "max_participants: 250",
        "The LiveKit ceiling is 250; the resolved Hub profile remains the admission authority and this is not capacity evidence.",
    ),
    Probe(
        "livekit_server_sdk", "sfu", "python_dependency", "missing",
        "requirements.txt", "livekit",
        "The Hub Python dependency manifest has no LiveKit server SDK.", False,
    ),
    Probe(
        "media_publication_schema", "media", "schema", "present",
        "schemas/webrtc/media_publication.v1.json", "Hub-authorized SFU media publication",
        "Publication audience, epoch, revision and privacy are explicitly schema-bound.",
    ),
    Probe(
        "media_subscription_schema", "media", "schema", "present",
        "schemas/webrtc/media_subscription.v1.json", "Hub-authorized SFU media subscription",
        "Subscriber, publisher, publication, membership epoch and revision are schema-bound.",
    ),
    Probe(
        "node_agent_or_fleet_control", "sfu", "python_dependency", "missing",
        "pyproject.toml", "livekit-api",
        "No authenticated LiveKit node-agent/server-control dependency is declared.", False,
    ),
    Probe(
        "observability_boundary", "observability", "operations", "partial",
        "docs/privacy/semantic-media-observability.md", None,
        "Content-safe semantic-media observability rules exist; broadcast fleet telemetry does not.",
    ),
    Probe(
        "operations_sfu_runbook", "sfu", "operations", "present",
        "docs/operations/semantic-media-sfu.md", None,
        "The opt-in SFU lifecycle, fallback and operational boundary are documented.",
    ),
    Probe(
        "pair_media_tracks", "pair", "production", "present",
        "frontend-angular/src/app/services/webrtc-media-session.service.ts", "WebrtcMediaSessionService",
        "The productive Pair path owns browser media capture and peer media sessions.",
    ),
    Probe(
        "parent_no_go_observe_only", "governance", "decision", "partial",
        "todos/active/todo.webrtc-sfu-broadcast-fanout.json", '"parent_decision": "no_go"',
        "The tracked parent decision is no_go with observe_only rollout; implementation may remain flag-off only.",
    ),
    Probe(
        "parent_release_evidence", "governance", "test_gate", "partial",
        "artifacts/test-gates/semantic-media-program-evidence.json", None,
        "A real parent gate artifact is referenced by path; no Source or Run ID is synthesized.",
    ),
    Probe(
        "persistent_sfu_admission_migration", "sfu", "migration", "present",
        "migrations/versions/e6f7a8b9c0d1_add_semantic_sfu_admission_state.py", None,
        "Persistent SFU admission state has a repository migration.",
    ),
    Probe(
        "publisher_subscription_permissions", "sfu", "production", "present",
        "frontend-angular/src/app/services/livekit-sfu-room.adapter.ts", "setTrackSubscriptionPermissions",
        "The publisher projects Hub-authorized receiver IDs through LiveKit track subscription permissions.",
    ),
    Probe(
        "relay_shared_store", "relay", "production", "present",
        "agent/repositories/semantic_relay_shared_store.py", "SharedSemanticRelayRepository",
        "The encrypted relay has a shared relational repository rather than an implicit process-local loop.",
    ),
    Probe(
        "semantic_media_contract_migration", "media", "migration", "present",
        "migrations/versions/c7d8e9f0a1b2_add_semantic_media_contracts.py", None,
        "Publication/subscription contract persistence has a repository migration.",
    ),
    Probe(
        "sfu_failover_test", "sfu", "test", "partial",
        "tests/chaos/test_semantic_sfu_failover.py", None,
        "Parent failover coverage exists, but does not select or prove a broadcast fleet runtime boundary.",
    ),
    Probe(
        "sfu_frame_crypto_helper_test_only", "e2ee", "test_helper", "partial",
        "frontend-angular/src/app/services/sfu-media-frame-crypto.service.ts", "SfuMediaFrameCryptoService",
        "The custom frame-crypto helper is exercised by its spec only; productive E2EE uses BaseKeyProvider.",
    ),
    Probe(
        "sfu_three_peer_test", "sfu", "test", "partial",
        "tests/e2e/test_semantic_sfu_spike.py", '"receivers": 2',
        "Committed live evidence covers one publisher and two receivers, not broadcast capacity.",
    ),
    Probe(
        "signaling_blueprint", "signaling", "production", "present",
        "agent/routes/webrtc_signaling.py", "Blueprint",
        "Hub-side WebRTC signaling remains an explicit route boundary.",
    ),
    Probe(
        "webrtc_pair_architecture", "pair", "operations", "present",
        "docs/architecture/pair-view-sync.md", None,
        "Pair WebRTC/DataChannel and Hub Relay fallback flows are documented separately.",
    ),
)


def _read(path: Path) -> str:
    if not path.is_file():
        raise AuditFailure(f"missing_required_path:{path.as_posix()}")
    return path.read_text(encoding="utf-8", errors="strict")


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256(path: Path) -> str:
    if not path.is_file():
        raise AuditFailure(f"missing_required_path:{path.as_posix()}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_dir(root: Path) -> Path:
    marker = root / ".git"
    if marker.is_dir():
        return marker
    if marker.is_file():
        value = _read(marker).strip()
        if value.startswith("gitdir: "):
            candidate = Path(value.removeprefix("gitdir: "))
            return candidate if candidate.is_absolute() else (root / candidate).resolve()
    raise AuditFailure("missing_required_path:.git/HEAD")


def _source_commit(root: Path) -> str:
    git_dir = _git_dir(root)
    head = _read(git_dir / "HEAD").strip()
    if not head.startswith("ref: "):
        commit = head
    else:
        ref = head.removeprefix("ref: ")
        loose_ref = git_dir / ref
        if loose_ref.is_file():
            commit = _read(loose_ref).strip()
        else:
            packed = _read(git_dir / "packed-refs")
            commit = next((line.split(" ", 1)[0] for line in packed.splitlines() if line.endswith(f" {ref}")), "")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise AuditFailure("invalid_source_commit")
    return commit


def _validate_probe(root: Path, probe: Probe) -> dict:
    if probe.status not in CLASSIFICATIONS:
        raise AuditFailure(f"unsupported_status:{probe.finding}:{probe.status}")
    path = root / probe.path
    content = _read(path)
    observed = probe.symbol is None or probe.symbol in content
    if observed != probe.expected_present:
        reason = "expected_source_missing" if probe.expected_present else "contradictory_status_claim"
        raise AuditFailure(f"{reason}:{probe.finding}:{probe.path}:{probe.symbol or 'file'}")
    row = asdict(probe)
    row.pop("expected_present")
    return row


def _validate_test_only_crypto_helper(root: Path) -> None:
    source_root = root / "frontend-angular/src/app"
    symbol = "SfuMediaFrameCryptoService"
    definition = "frontend-angular/src/app/services/sfu-media-frame-crypto.service.ts"
    test_references = 0
    production_references: list[str] = []
    for path in sorted(source_root.rglob("*.ts")):
        relative = _relative(path, root)
        if symbol not in path.read_text(encoding="utf-8", errors="strict") or relative == definition:
            continue
        if relative.endswith(".spec.ts"):
            test_references += 1
        else:
            production_references.append(relative)
    if test_references == 0:
        raise AuditFailure("expected_source_missing:sfu_frame_crypto_helper_test_only:spec_reference")
    if production_references:
        raise AuditFailure(
            "contradictory_status_claim:sfu_frame_crypto_helper_test_only:" + ",".join(production_references)
        )


def _validate_missing_server_runtime(root: Path) -> None:
    patterns = ("from livekit", "import livekit", "LiveKitAPI", "RoomServiceClient")
    candidates = [root / "pyproject.toml", root / "requirements.txt"]
    agent_root = root / "agent"
    if agent_root.is_dir():
        candidates.extend(sorted(agent_root.rglob("*.py")))
    conflicts = [
        _relative(path, root)
        for path in candidates
        if path.is_file() and any(pattern in path.read_text(encoding="utf-8", errors="strict") for pattern in patterns)
    ]
    if conflicts:
        raise AuditFailure("contradictory_status_claim:livekit_server_sdk:" + ",".join(conflicts))


def _parent_readiness(root: Path, expected_decision: str, expected_stage: str) -> dict:
    todo_path = root / "todos/active/todo.webrtc-sfu-broadcast-fanout.json"
    todo = json.loads(_read(todo_path))
    readiness = todo.get("activation_readiness") or {}
    observed_decision = readiness.get("parent_decision")
    observed_stage = readiness.get("parent_rollout_stage")
    if observed_decision != expected_decision or observed_stage != expected_stage:
        raise AuditFailure(
            "contradictory_status_claim:parent_readiness:"
            f"expected={expected_decision}/{expected_stage}:observed={observed_decision}/{observed_stage}"
        )
    source_artifact = readiness.get("source_artifact")
    if not isinstance(source_artifact, str) or not (root / source_artifact).is_file():
        raise AuditFailure("missing_required_path:parent_readiness_source_artifact")
    return {
        "decision": observed_decision,
        "rollout_stage": observed_stage,
        "source_artifact": source_artifact,
    }


def build_report(
    root: Path = ROOT,
    *,
    expected_parent_decision: str = "no_go",
    expected_parent_rollout_stage: str = "observe_only",
) -> dict:
    """Return a stable report; fail when source evidence contradicts a claim."""
    root = root.resolve()
    findings = sorted((_validate_probe(root, probe) for probe in PROBES), key=lambda row: row["finding"])
    _validate_test_only_crypto_helper(root)
    _validate_missing_server_runtime(root)
    return {
        "schema": "ananta.webrtc-sfu-broadcast-baseline.v1",
        "tool_version": TOOL_VERSION,
        "source_commit": _source_commit(root),
        "source_id_policy": "repository_path_and_symbol_only; no_source_or_run_ids_synthesized",
        "classifications": sorted(CLASSIFICATIONS),
        "config_digests": [
            {"path": path, "sha256": _sha256(root / path)} for path in sorted(CONFIG_PATHS)
        ],
        "parent_readiness": _parent_readiness(
            root, expected_parent_decision, expected_parent_rollout_stage
        ),
        "findings": findings,
    }


def _serialize(report: dict) -> str:
    return json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="compare the tracked report")
    action.add_argument("--write", action="store_true", help="write the tracked report")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository or deterministic fixture root")
    parser.add_argument("--output", type=Path, help="override the report path")
    parser.add_argument("--expect-parent-decision", default="no_go")
    parser.add_argument("--expect-parent-rollout-stage", default="observe_only")
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output or (root / OUTPUT_RELATIVE)
    try:
        report_text = _serialize(build_report(
            root,
            expected_parent_decision=args.expect_parent_decision,
            expected_parent_rollout_stage=args.expect_parent_rollout_stage,
        ))
    except (AuditFailure, json.JSONDecodeError) as error:
        print(json.dumps({"reason_code": "webrtc_sfu_broadcast_baseline_invalid", "detail": str(error)}, sort_keys=True))
        return 2
    if args.write:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report_text, encoding="utf-8")
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != report_text:
            print(json.dumps({"path": _relative(output, root), "reason_code": "webrtc_sfu_broadcast_baseline_stale"}, sort_keys=True))
            return 1
    if not args.write and not args.check:
        print(report_text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
