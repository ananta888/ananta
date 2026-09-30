"""Paged snapshot projection (timeline, transcript, resume cursor) of live runs."""

from __future__ import annotations

from typing import Any, Mapping

from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal
from agent.services.voice_live_run_contracts import MAX_TIMELINE_ITEMS
from agent.services.voice_live_run_timeline import (
    acknowledged_through,
    compose_transcript,
    gap_sequences,
    public_run,
    timeline_items,
)
from agent.services.voice_result_artifact_service import VoiceResultArtifactService


class VoiceLiveRunSnapshotBuilder:
    """Project persisted run rows and decrypted segment results into the API view."""

    def __init__(self, *, artifacts: VoiceResultArtifactService) -> None:
        self._artifacts = artifacts

    def build(
        self,
        principal: VoicePrincipal,
        run: VoiceLiveRunDB,
        segments: tuple[VoiceLiveRunSegmentDB, ...],
        *,
        after_sequence: int,
        after_revision: int | None,
        limit: int,
        include_text: bool,
    ) -> dict[str, Any]:
        gaps = gap_sequences(run, segments)
        bounded_limit = max(1, min(int(limit), MAX_TIMELINE_ITEMS))
        if after_revision is None:
            result_by_sequence = self._load_segment_results(
                principal,
                segments,
                include_text=include_text,
            )
            all_items = timeline_items(
                segments,
                result_by_sequence,
                gaps,
                gap_timeline_revision=run.timeline_revision,
            )
            page_candidates = [
                item for item in all_items if int(item["sequence"]) > int(after_sequence)
            ]
            page = page_candidates[:bounded_limit]
            next_after_revision = None
            completed_texts = [
                (
                    segment,
                    str((result_by_sequence.get(segment.sequence) or {}).get("text") or ""),
                )
                for segment in segments
                if segment.status == "completed"
            ]
            composed = compose_transcript(completed_texts)
        else:
            normalized_revision = max(-1, int(after_revision))
            # Revision polling deliberately excludes synthetic gaps: the full
            # gap set is returned separately and has no stable row identity.
            metadata_items = timeline_items(
                segments,
                {},
                [],
                gap_timeline_revision=run.timeline_revision,
            )
            page_candidates = [
                item
                for item in metadata_items
                if int(item.get("timeline_revision") or 0) > normalized_revision
            ]
            page_candidates.sort(
                key=lambda item: (
                    int(item.get("timeline_revision") or 0),
                    int(item["sequence"]),
                )
            )
            page = page_candidates[:bounded_limit]
            selected_sequences = {int(item["sequence"]) for item in page}
            selected_segments = tuple(
                segment for segment in segments if segment.sequence in selected_sequences
            )
            result_by_sequence = self._load_segment_results(
                principal,
                selected_segments,
                include_text=include_text,
            )
            for item in page:
                result = result_by_sequence.get(int(item["sequence"])) or {}
                item["text"] = result.get("text")
                item["provider"] = result.get("provider")
                item["model"] = result.get("model")
            next_after_revision = (
                max(int(item.get("timeline_revision") or normalized_revision) for item in page)
                if page
                else normalized_revision
            )
            composed = None
        acknowledged = acknowledged_through(segments)
        next_sequence = acknowledged + 1
        max_sequence = max(
            [
                *[segment.sequence for segment in segments],
                *gaps,
                int(run.last_local_sequence if run.last_local_sequence is not None else -1),
            ],
            default=-1,
        )
        return {
            "run": public_run(run),
            "segments": page,
            "composed_transcript": composed if include_text and after_revision is None else None,
            "gaps": gaps,
            "resume": {
                "acknowledged_through_sequence": acknowledged,
                "next_sequence": next_sequence,
                "last_seen_sequence": max_sequence,
                "pending_sequences": [segment.sequence for segment in segments if segment.status == "processing"],
                "failed_sequences": [segment.sequence for segment in segments if segment.status == "failed"],
                "pending_correction_sequences": [
                    segment.sequence
                    for segment in segments
                    if segment.correction_status in {"queued", "processing"}
                ],
            },
            "page": {
                "after_sequence": int(after_sequence),
                "after_revision": int(after_revision) if after_revision is not None else None,
                "limit": bounded_limit,
                "has_more": len(page_candidates) > len(page),
                "next_after_sequence": int(page[-1]["sequence"]) if page else int(after_sequence),
                "next_after_revision": next_after_revision,
            },
        }

    def _load_segment_results(
        self,
        principal: VoicePrincipal,
        segments: tuple[VoiceLiveRunSegmentDB, ...],
        *,
        include_text: bool,
    ) -> dict[int, dict[str, Any]]:
        results: dict[int, dict[str, Any]] = {}
        if not include_text:
            return results
        for segment in segments:
            if segment.status != "completed" or not segment.result_ref:
                continue
            try:
                artifact = self._artifacts.get(principal, segment.result_ref)
            except VoiceGovernanceError:
                continue
            result_value = artifact.get("result")
            result = dict(result_value) if isinstance(result_value, Mapping) else {}
            results[segment.sequence] = {
                "text": str(result.get("transcript") or result.get("text") or "").strip(),
                "provider": result.get("provider"),
                "model": result.get("model"),
            }
        return results


__all__ = ["VoiceLiveRunSnapshotBuilder"]
