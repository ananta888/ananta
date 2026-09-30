#!/usr/bin/env python3
"""Build the deterministic semantic-media/speech source baseline."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/domain/semantic-media-speech-baseline.json"
CLASSIFICATIONS = frozenset({"implemented", "partial", "stub", "absent", "unverified"})


@dataclass(frozen=True, slots=True)
class Probe:
    finding: str
    classification: str
    path: str
    symbol: str
    evidence: str
    expected_present: bool = True


PROBES = (
    Probe(
        "permission_key_drift",
        "implemented",
        "agent/services/share_session_permissions.py",
        "DEFAULT_PERMISSIONS",
        "The Hub owns the canonical v1 permission keys and the bounded legacy-alias migration.",
    ),
    Probe(
        "permission_key_drift_ui",
        "implemented",
        "frontend-angular/src/app/services/permission-labels.ts",
        "PERMISSION_LABELS",
        "Angular uses the same canonical v1 keys and maps legacy aliases only through one explicit boundary.",
    ),
    Probe(
        "pair_payload_e2ee",
        "implemented",
        "frontend-angular/src/app/services/pair-view-sync.service.ts",
        "cryptoPort.seal",
        "Pair payloads pass through the bound AEAD crypto port before transport.",
    ),
    Probe(
        "voice_chunk_reassembly_scope",
        "partial",
        "voice_runtime/streaming.py",
        "StreamSessionManager",
        "Chunk sessions are bounded but owned by one process-local runtime registry.",
    ),
    Probe(
        "hub_relay_durability",
        "implemented",
        "agent/repositories/semantic_relay_shared_store.py",
        "SharedSemanticRelayRepository",
        "The bounded encrypted relay uses a relational multi-Hub store with atomic cursors.",
    ),
    Probe(
        "browser_media_tracks",
        "implemented",
        "frontend-angular/src/app/services/webrtc-media-session.service.ts",
        "WebrtcMediaSessionService",
        "A production Pair service owns explicit capture, addTrack/ontrack, replacement, reconnect and cleanup.",
    ),
    Probe(
        "sfu_control_plane",
        "implemented",
        "agent/services/semantic_sfu_admission_service.py",
        "SemanticSfuAdmissionService",
        "The Hub owns bounded SFU admission, membership epochs, revisions and publication/subscription grants.",
    ),
    Probe(
        "voice_alignment",
        "implemented",
        "voice_runtime/fusion/alignment.py",
        "align_candidates",
        "Deterministic transcript candidate alignment exists in the Voice Runtime.",
    ),
    Probe(
        "voice_consent_categories",
        "implemented",
        "ananta_contracts/speech_evidence_governance.py",
        "SpeechEvidenceConsent",
        "Speech evidence consent independently binds raw-audio sharing, peer use, dataset and training grants.",
    ),
    Probe(
        "speech_training_boundary",
        "implemented",
        "worker/speech_training/contracts.py",
        "CONTRACT_VERSION",
        "The isolated speech Worker imports the shared, versioned speech-adaptation job/result contract.",
    ),
)


def _contains_symbol(path: Path, symbol: str) -> bool:
    return path.exists() and symbol in path.read_text(encoding="utf-8", errors="strict")


def build_report() -> dict:
    findings: list[dict] = []
    for probe in PROBES:
        if probe.classification not in CLASSIFICATIONS:
            raise ValueError(f"unsupported_classification:{probe.classification}")
        path = ROOT / probe.path
        observed = _contains_symbol(path, probe.symbol)
        if observed != probe.expected_present:
            reason = "expected_source_missing" if probe.expected_present else "expected_absence_changed"
            raise ValueError(f"{reason}:{probe.path}:{probe.symbol}")
        row = asdict(probe)
        row.pop("expected_present")
        findings.append(row)
    return {
        "schema": "ananta.semantic-media-speech-baseline.v1",
        "source_id_policy": "repository_path_and_symbol_only",
        "classifications": sorted(CLASSIFICATIONS),
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="compare the tracked report")
    parser.add_argument("--write", action="store_true", help="write the tracked report")
    args = parser.parse_args()
    report_text = json.dumps(build_report(), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(report_text, encoding="utf-8")
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != report_text:
            print(json.dumps({"reason_code": "semantic_media_baseline_stale", "path": str(OUTPUT.relative_to(ROOT))}))
            return 1
    if not args.write and not args.check:
        print(report_text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
