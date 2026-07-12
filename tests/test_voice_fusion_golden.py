from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from voice_runtime.backends.base import (
    TranscriptionCandidate,
    TranscriptionSegment,
    TranscriptionWord,
)
from voice_runtime.fusion.alignment import align_tokens_to_anchor, tokenize
from voice_runtime.fusion.consensus import DeterministicFusionService
from voice_runtime.fusion.scoring import CandidateScorer

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "voice" / "fusion-golden.v1.json"


def _word(raw: Mapping[str, Any]) -> TranscriptionWord:
    return TranscriptionWord(
        start_ms=int(raw["start_ms"]),
        end_ms=int(raw["end_ms"]),
        text=str(raw["text"]),
        confidence=float(raw["confidence"]) if raw.get("confidence") is not None else None,
    )


def _segment(raw: Mapping[str, Any]) -> TranscriptionSegment:
    return TranscriptionSegment(
        start_ms=int(raw["start_ms"]),
        end_ms=int(raw["end_ms"]),
        text=str(raw["text"]),
        confidence=float(raw["confidence"]) if raw.get("confidence") is not None else None,
        words=tuple(_word(item) for item in raw.get("words") or ()),
    )


def _candidate(raw: Mapping[str, Any], language: str) -> TranscriptionCandidate:
    words = tuple(_word(item) for item in raw.get("words") or ())
    segments = tuple(_segment(item) for item in raw.get("segments") or ())
    if words and not segments:
        segments = (
            TranscriptionSegment(
                start_ms=min(item.start_ms for item in words),
                end_ms=max(item.end_ms for item in words),
                text=str(raw["text"]),
                confidence=float(raw["confidence"]),
                words=words,
            ),
        )
    return TranscriptionCandidate(
        candidate_id=str(raw["candidate_id"]),
        backend=str(raw["backend"]),
        model=str(raw["model"]),
        model_revision=str(raw["model_revision"]),
        manifest_digest=str(raw["manifest_digest"]),
        text=str(raw["text"]),
        words=words,
        segments=segments,
        language=language,
        duration_ms=int(raw["duration_ms"]),
        confidence=float(raw["confidence"]),
        source_audio_digest="sha256:" + "e" * 64,
        lineage_id=str(raw["candidate_id"]),
    )


def _projection(candidates: tuple[TranscriptionCandidate, ...]) -> dict[str, Any]:
    scorer = CandidateScorer()
    outcome = DeterministicFusionService(scorer).fuse(candidates)
    anchor = next(item for item in candidates if item.candidate_id == outcome.result.selected_candidate_id)
    anchor_tokens = tokenize(anchor.text)
    return {
        "alignment": {
            candidate.candidate_id: [
                asdict(item)
                for item in align_tokens_to_anchor(anchor_tokens, tokenize(candidate.text))
            ]
            for candidate in candidates
        },
        "candidate_scores": {
            candidate.candidate_id: scorer.score(candidate)[1]
            for candidate in candidates
        },
        "disagreement_regions": [item.as_dict() for item in outcome.result.disagreement_regions],
        "fusion_provenance": dict(outcome.result.provenance),
        "result_hash": outcome.result_hash,
        "selected_candidate_id": outcome.result.selected_candidate_id,
        "text": outcome.result.text,
        "token_provenance": list(outcome.result.decision_trace["token_provenance"]),
        "words": [word.as_dict() for segment in outcome.result.segments for word in segment.words],
    }


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_fusion_goldens_are_complete_provenanced_and_bit_stable() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == "ananta.voice-fusion-golden.v1"
    assert fixture["fixture_license"] == "CC0-1.0"
    all_phenomena: set[str] = set()
    languages: set[str] = set()

    for case in fixture["cases"]:
        languages.add(case["language"])
        all_phenomena.update(case["phenomena"])
        candidates = tuple(_candidate(raw, case["language"]) for raw in case["candidates"])
        first = _projection(candidates)
        second = _projection(candidates)
        expected = case["expected"]

        assert first == second
        assert first["text"] == expected["text"]
        assert first["selected_candidate_id"] == expected["selected_candidate_id"]
        provenance_projection = [
            {
                key: item[key]
                for key in (
                    "token_index",
                    "token",
                    "candidate_id",
                    "source_token_index",
                    "lineage_id",
                    "operation",
                )
            }
            for item in first["token_provenance"]
        ]
        assert provenance_projection == expected["token_provenance"]
        assert _digest(first) == expected["projection_sha256"]
        known_ids = {candidate.candidate_id for candidate in candidates}
        assert all(item["candidate_id"] in known_ids for item in first["token_provenance"])
        assert all(
            item["time_source"] in {"word", "segment_bounds", "unavailable"}
            for item in first["token_provenance"]
        )
        assert all(
            item["start_ms"] is None and item["end_ms"] is None
            for item in first["token_provenance"]
            if item["time_source"] == "unavailable"
        )
        assert all(item["source_backend"] for item in first["token_provenance"])
        assert "synthetic" in first["fusion_provenance"]

    assert languages == {"de", "en", "de-en"}
    assert {
        "delete",
        "homophone",
        "insert",
        "missing_times",
        "mixed_language",
        "name",
        "number",
        "overlap",
    }.issubset(all_phenomena)


def test_fusion_golden_projection_is_identical_across_hash_seeds() -> None:
    script = """
import json, runpy
from pathlib import Path
ns = runpy.run_path('tests/test_voice_fusion_golden.py')
fixture = json.loads(Path('tests/fixtures/voice/fusion-golden.v1.json').read_text(encoding='utf-8'))
print(json.dumps({
    case['case_id']: ns['_digest'](ns['_projection'](tuple(
        ns['_candidate'](raw, case['language']) for raw in case['candidates']
    )))
    for case in fixture['cases']
}, sort_keys=True))
"""
    results = []
    for seed in ("1", "777"):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        results.append(json.loads(completed.stdout))
    assert results[0] == results[1]
