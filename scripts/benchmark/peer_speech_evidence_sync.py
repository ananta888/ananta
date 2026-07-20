#!/usr/bin/env python3
"""Fail-closed M9 benchmark for peer speech-evidence enrichment.

The evaluator deliberately does not contain demo measurements.  A successful
report can only be produced from an explicit measurement document that is
bound to the current benchmark sources and configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Mapping

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.services.semantic_media_program_evidence import (  # noqa: E402
    GateEvidence,
    ProgramEvidenceError,
    canonical_sha256,
    source_hash,
    unavailable_evidence,
    write_report,
)
from voice_runtime.backends.base import TranscriptionCandidate  # noqa: E402
from voice_runtime.fusion.alignment import align_candidates  # noqa: E402
from voice_runtime.peer_transcript_consensus import (  # noqa: E402
    PeerTranscriptCandidate,
    PeerTranscriptConflictGraphBuilder,
)
from voice_runtime.peer_transcript_resolution import PeerTranscriptResolutionService  # noqa: E402

GATE_ID = "m9_peer_sync_performance"
MEASUREMENT_SCHEMA = "ananta.peer-speech-sync-measurements.v1"
CONFIG_PATH = "config/peer-speech-evidence-sync-benchmark.v1.json"
SOURCE_PATHS = (
    "ananta_contracts/speech_evidence_resolution.py",
    "ananta_contracts/speech_evidence_sync.py",
    "frontend-angular/src/app/features/voice/peer-evidence-sync.facade.ts",
    "frontend-angular/src/app/services/speech-evidence-datachannel-transport.service.ts",
    "scripts/benchmark/peer_speech_evidence_sync.py",
    "voice_runtime/backends/base.py",
    "voice_runtime/fusion/alignment.py",
    "voice_runtime/peer_transcript_consensus.py",
    "voice_runtime/peer_transcript_resolution.py",
)

_EXECUTION_MODES = ("local_only", "peer_enriched", "blind_merge")


@dataclass(frozen=True, slots=True)
class BenchmarkMode:
    mode: str
    lexical_error_micros: int
    leakage_events: int
    transcript_p95_ms: int
    sample_count: int = 1

    def __post_init__(self) -> None:
        if self.mode not in {"local_only", "peer_enriched", "blind_merge"}:
            raise ProgramEvidenceError("speech_sync_benchmark_mode_invalid")
        for value in (
            self.lexical_error_micros,
            self.leakage_events,
            self.transcript_p95_ms,
            self.sample_count,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ProgramEvidenceError("speech_sync_benchmark_measurement_invalid")
        if self.lexical_error_micros > 1_000_000 or self.transcript_p95_ms < 1 or self.sample_count < 1:
            raise ProgramEvidenceError("speech_sync_benchmark_measurement_invalid")


@dataclass(frozen=True, slots=True)
class BenchmarkPolicy:
    maximum_peer_leakage_events: int
    maximum_p95_regression_micros: int
    minimum_samples_per_mode: int
    forbid_peer_quality_regression: bool


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate(
    *,
    candidate_id: str,
    text: str,
    source_id: str,
    sample_index: int,
) -> PeerTranscriptCandidate:
    """Build one bounded synthetic candidate for component-level measurement.

    Synthetic tokens keep the benchmark independent of personal speech data. The
    production conflict graph and resolution services still receive their normal
    domain objects, provenance and confidence fields.
    """

    transcript = TranscriptionCandidate(
        candidate_id=candidate_id,
        backend="benchmark-fixture",
        model="content-free-synthetic-v1",
        synthetic=True,
        text=text,
        confidence=0.96,
        status="succeeded",
    )
    return PeerTranscriptCandidate(
        transcript=transcript,
        source_id=source_id,
        source_family="content-free-synthetic",
        contributor_digest=_digest(f"contributor:{source_id}"),
        revision=sample_index + 1,
        lineage_digest=_digest(f"lineage:{source_id}:{sample_index}"),
        signature_digest=_digest(f"signature:{source_id}:{sample_index}:{candidate_id}"),
        authority_micros=850_000,
        quality_micros=850_000,
    )


def _lexical_error_micros(
    reference: TranscriptionCandidate,
    candidate: TranscriptionCandidate,
) -> int:
    """Measure aligned token edit mass through the canonical fusion component."""

    alignment = align_candidates(reference, candidate)
    changed_tokens = sum(
        max(
            span.reference_end_index - span.reference_start_index,
            span.candidate_end_index - span.candidate_start_index,
        )
        for span in alignment.spans
        if span.operation != "equal"
    )
    return min(
        1_000_000,
        changed_tokens * 1_000_000 // max(1, len(alignment.reference_tokens)),
    )


def _nearest_rank_p95_ms(elapsed_ns: list[int]) -> int:
    ordered = sorted(elapsed_ns)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return max(1, math.ceil(ordered[index] / 1_000_000))


def _run_component_sample(mode: str, sample_index: int) -> tuple[int, int, int]:
    """Run product reconciliation components once and return aggregates only.

    ``blind_merge`` is an intentionally unsafe benchmark control. It is kept in
    this script and is never exposed as a production resolution strategy.
    """

    canary = f"privatecanary{sample_index:06d}"
    golden = TranscriptionCandidate(
        candidate_id=f"golden-{sample_index}",
        backend="benchmark-fixture",
        synthetic=True,
        text=f"{canary} nord wind klar heute.",
        confidence=0.99,
    )
    local = _candidate(
        candidate_id=f"z-local-{sample_index}",
        text=f"{canary} nord wind klar heute",
        source_id="local",
        sample_index=sample_index,
    )
    peer = _candidate(
        candidate_id=f"a-peer-{sample_index}",
        text=golden.text,
        source_id="peer",
        sample_index=sample_index,
    )
    candidates = (local,) if mode == "local_only" else (local, peer)

    started_ns = perf_counter_ns()
    graph = PeerTranscriptConflictGraphBuilder().build(candidates)
    resolution = PeerTranscriptResolutionService().resolve(graph)
    by_id = {item.transcript.candidate_id: item.transcript for item in candidates}
    selected_ids = {
        decision.selected_candidate_id
        for decision in resolution.decisions
        if decision.selected_candidate_id is not None
    }
    if mode == "peer_enriched" and len(selected_ids) == 1:
        selected = by_id[next(iter(selected_ids))]
    elif mode == "blind_merge":
        # Deliberately poor comparator used only to prove that the gate detects
        # uncurated aggregation. The production resolver above is still run.
        selected = TranscriptionCandidate(
            candidate_id=f"blind-{sample_index}",
            backend="benchmark-only-unsafe-control",
            synthetic=True,
            text=f"{local.transcript.text} {peer.transcript.text}",
            confidence=0.0,
        )
    else:
        selected = local.transcript
    error_micros = _lexical_error_micros(golden, selected)
    elapsed_ns = perf_counter_ns() - started_ns

    # Only this bounded aggregate surface is eligible for serialization. A hit
    # demonstrates that private candidate content escaped the benchmark adapter.
    public_surface = json.dumps(
        {
            "mode_ordinal": _EXECUTION_MODES.index(mode),
            "error_micros": error_micros,
            "elapsed_ns": elapsed_ns,
            "decision_count": len(resolution.decisions),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    leakage_events = int(canary in public_surface)
    return error_micros, leakage_events, elapsed_ns


def execute_measurement_document(
    *,
    root: Path = _PROJECT_ROOT,
    sample_count: int | None = None,
) -> dict[str, object]:
    """Execute a bounded component benchmark and bind all aggregate rows."""

    policy = load_policy(root)
    count = policy.minimum_samples_per_mode if sample_count is None else sample_count
    if isinstance(count, bool) or not isinstance(count, int) or count < policy.minimum_samples_per_mode:
        raise ProgramEvidenceError("speech_sync_benchmark_sample_count_insufficient")

    rows: list[dict[str, int | str]] = []
    for mode in _EXECUTION_MODES:
        errors: list[int] = []
        leakage_events = 0
        elapsed: list[int] = []
        for sample_index in range(count):
            error_micros, leaked, elapsed_ns = _run_component_sample(mode, sample_index)
            errors.append(error_micros)
            leakage_events += leaked
            elapsed.append(elapsed_ns)
        rows.append(
            {
                "mode": mode,
                "lexical_error_micros": sum(errors) // len(errors),
                "leakage_events": leakage_events,
                "transcript_p95_ms": _nearest_rank_p95_ms(elapsed),
                "sample_count": count,
            }
        )
    source_digest, config_digest = benchmark_binding(root)
    return {
        "schema": MEASUREMENT_SCHEMA,
        "source_sha256": source_digest,
        "config_sha256": config_digest,
        "run_id_sha256": measurement_run_id(
            source_sha256=source_digest,
            config_sha256=config_digest,
            modes=rows,
        ),
        "modes": rows,
    }


def benchmark_binding(root: Path = _PROJECT_ROOT) -> tuple[str, str]:
    """Return the exact source/config digests a measurement run must record."""

    return source_hash(root, SOURCE_PATHS), source_hash(root, (CONFIG_PATH,))


def measurement_run_id(
    *,
    source_sha256: str,
    config_sha256: str,
    modes: list[Mapping[str, Any]],
) -> str:
    """Bind a measurement identity to every decision-relevant input row."""

    return canonical_sha256(
        {
            "schema": MEASUREMENT_SCHEMA,
            "source_sha256": source_sha256,
            "config_sha256": config_sha256,
            "modes": modes,
        }
    )


def load_policy(root: Path = _PROJECT_ROOT) -> BenchmarkPolicy:
    try:
        raw = json.loads((root / CONFIG_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProgramEvidenceError("speech_sync_benchmark_config_unavailable") from exc
    expected = {
        "schema",
        "required_modes",
        "maximum_peer_leakage_events",
        "maximum_p95_regression_micros",
        "minimum_samples_per_mode",
        "forbid_peer_quality_regression",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise ProgramEvidenceError("speech_sync_benchmark_config_invalid")
    if raw.get("schema") != "ananta.peer-speech-sync-benchmark-policy.v1" or raw.get("required_modes") != [
        "local_only",
        "peer_enriched",
        "blind_merge",
    ]:
        raise ProgramEvidenceError("speech_sync_benchmark_config_invalid")
    integers = (
        raw.get("maximum_peer_leakage_events"),
        raw.get("maximum_p95_regression_micros"),
        raw.get("minimum_samples_per_mode"),
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in integers):
        raise ProgramEvidenceError("speech_sync_benchmark_config_invalid")
    if raw.get("forbid_peer_quality_regression") is not True or int(integers[2]) < 1:
        raise ProgramEvidenceError("speech_sync_benchmark_config_invalid")
    return BenchmarkPolicy(
        maximum_peer_leakage_events=int(integers[0]),
        maximum_p95_regression_micros=int(integers[1]),
        minimum_samples_per_mode=int(integers[2]),
        forbid_peer_quality_regression=True,
    )


def evaluate(
    rows: tuple[BenchmarkMode, ...],
    policy: BenchmarkPolicy | None = None,
) -> dict[str, object]:
    """Evaluate explicit measurements; this function never supplies them."""

    active_policy = policy or BenchmarkPolicy(0, 50_000, 1, True)
    by_mode = {row.mode: row for row in rows}
    required = {"local_only", "peer_enriched", "blind_merge"}
    if len(by_mode) != len(rows) or set(by_mode) != required:
        raise ProgramEvidenceError("speech_sync_benchmark_modes_invalid")
    baseline = by_mode["local_only"]
    peer = by_mode["peer_enriched"]
    p95_regression_micros = (
        (peer.transcript_p95_ms - baseline.transcript_p95_ms) * 1_000_000
        // max(1, baseline.transcript_p95_ms)
    )
    reasons: list[str] = []
    if any(row.sample_count < active_policy.minimum_samples_per_mode for row in rows):
        reasons.append("speech_sync_benchmark_sample_count_insufficient")
    if peer.leakage_events > active_policy.maximum_peer_leakage_events:
        reasons.append("speech_sync_leakage_detected")
    if active_policy.forbid_peer_quality_regression and peer.lexical_error_micros > baseline.lexical_error_micros:
        reasons.append("speech_sync_quality_regression")
    if p95_regression_micros > active_policy.maximum_p95_regression_micros:
        reasons.append("speech_sync_transcript_p95_regression")
    return {
        "version": "ananta.peer-speech-sync-benchmark.v2",
        "passed": not reasons,
        "reason_codes": sorted(set(reasons)),
        "p95_regression_micros": p95_regression_micros,
        "modes": [asdict(row) for row in sorted(rows, key=lambda value: value.mode)],
        # This benchmark may reject a rollout, but it never creates a quality claim.
        "quality_claim": False,
    }


def evaluate_measurement_document(
    document: Mapping[str, Any],
    *,
    root: Path = _PROJECT_ROOT,
) -> GateEvidence:
    expected_fields = {
        "schema",
        "source_sha256",
        "config_sha256",
        "run_id_sha256",
        "modes",
    }
    if set(document) != expected_fields or document.get("schema") != MEASUREMENT_SCHEMA:
        raise ProgramEvidenceError("speech_sync_measurement_document_invalid")
    expected_source, expected_config = benchmark_binding(root)
    if document.get("source_sha256") != expected_source:
        raise ProgramEvidenceError("speech_sync_measurement_source_stale")
    if document.get("config_sha256") != expected_config:
        raise ProgramEvidenceError("speech_sync_measurement_config_stale")
    run_id = document.get("run_id_sha256")
    if (
        not isinstance(run_id, str)
        or len(run_id) != 64
        or any(character not in "0123456789abcdef" for character in run_id)
    ):
        raise ProgramEvidenceError("speech_sync_measurement_run_id_invalid")
    raw_modes = document.get("modes")
    if not isinstance(raw_modes, list):
        raise ProgramEvidenceError("speech_sync_measurement_document_invalid")
    if run_id != measurement_run_id(
        source_sha256=expected_source,
        config_sha256=expected_config,
        modes=raw_modes,
    ):
        raise ProgramEvidenceError("speech_sync_measurement_run_binding_mismatch")
    rows: list[BenchmarkMode] = []
    for raw in raw_modes:
        if not isinstance(raw, Mapping) or set(raw) != {
            "mode", "lexical_error_micros", "leakage_events", "transcript_p95_ms", "sample_count"
        }:
            raise ProgramEvidenceError("speech_sync_measurement_document_invalid")
        try:
            rows.append(BenchmarkMode(**dict(raw)))
        except TypeError as exc:
            raise ProgramEvidenceError("speech_sync_measurement_document_invalid") from exc
    result = evaluate(tuple(rows), load_policy(root))
    by_mode = {row.mode: row for row in rows}
    measurements: dict[str, int | bool | str] = {
        "measurement_run_id_sha256": run_id,
        "mode_count": len(rows),
        "local_sample_count": by_mode["local_only"].sample_count,
        "peer_sample_count": by_mode["peer_enriched"].sample_count,
        "blind_sample_count": by_mode["blind_merge"].sample_count,
        "local_lexical_error_micros": by_mode["local_only"].lexical_error_micros,
        "peer_lexical_error_micros": by_mode["peer_enriched"].lexical_error_micros,
        "peer_leakage_events": by_mode["peer_enriched"].leakage_events,
        "local_p95_ms": by_mode["local_only"].transcript_p95_ms,
        "peer_p95_ms": by_mode["peer_enriched"].transcript_p95_ms,
        "p95_regression_micros": int(result["p95_regression_micros"]),
        "quality_claim": False,
    }
    return GateEvidence(
        gate_id=GATE_ID,
        status="passed" if result["passed"] else "failed",
        reason_codes=tuple(str(value) for value in result["reason_codes"]),
        source_sha256=expected_source,
        config_sha256=expected_config,
        measurements=measurements,
    )


def _unavailable(reason_code: str, *, root: Path = _PROJECT_ROOT) -> GateEvidence:
    source_digest, config_digest = benchmark_binding(root)
    return unavailable_evidence(
        GATE_ID,
        source_sha256=source_digest,
        config_sha256=config_digest,
        reason_code=reason_code,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--measurements", type=Path, help="explicit source-bound measurement document")
    source.add_argument(
        "--execute",
        action="store_true",
        help="execute the bounded product-component benchmark locally",
    )
    parser.add_argument("--output", type=Path, help="write GateEvidence-v1 report atomically")
    parser.add_argument("--print-binding", action="store_true", help="print current source/config digests only")
    args = parser.parse_args()
    if args.print_binding:
        source_digest, config_digest = benchmark_binding()
        print(json.dumps({"source_sha256": source_digest, "config_sha256": config_digest}, sort_keys=True))
        return 0
    try:
        if args.execute:
            evidence = evaluate_measurement_document(execute_measurement_document())
        elif args.measurements is None:
            evidence = _unavailable("speech_sync_measurements_not_supplied")
        else:
            raw = json.loads(args.measurements.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                raise ProgramEvidenceError("speech_sync_measurement_document_invalid")
            evidence = evaluate_measurement_document(raw)
    except (OSError, json.JSONDecodeError, ProgramEvidenceError) as exc:
        reason_code = getattr(exc, "reason_code", "speech_sync_measurements_unavailable")
        try:
            evidence = _unavailable(str(reason_code))
        except ProgramEvidenceError:
            print(json.dumps({"status": "failed", "reason_code": str(reason_code)}, sort_keys=True))
            return 1
    if args.output is not None:
        write_report(args.output, evidence)
    print(json.dumps(evidence.as_document(), sort_keys=True, separators=(",", ":")))
    return 0 if evidence.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
