"""Transcript post-processing and Hub correction-boundary stages.

Post-processing applies glossary, Hub-supplied personalization and rule
edits locally. Restricted-choice and generative correction are never executed
here; the boundary stage only records that Hub post-processing is required.
"""

from __future__ import annotations

from dataclasses import replace

from .backends.base import TranscriptionResult
from .config import VoiceRuntimeConfig
from .context import VoiceRecognitionContext
from .execution_policy import VoiceExecutionPolicy
from .glossary import Glossary
from .postprocessing import build_postprocessor


class TranscriptPostprocessingStage:
    """Apply glossary, personalization and rule post-processing to a result."""

    def __init__(self, *, config: VoiceRuntimeConfig) -> None:
        self._config = config

    def apply(
        self,
        result: TranscriptionResult,
        *,
        policy: VoiceExecutionPolicy,
        context: VoiceRecognitionContext | None,
    ) -> tuple[TranscriptionResult, dict | None]:
        glossary = Glossary.load(self._config.glossary_path)
        personalization_metadata: dict[str, object] = {}
        if context is not None and (context.substitutions or context.preferences):
            weights = dict(context.personalization_weights)
            replacements = dict(glossary.replacements)
            if weights.get("substitution", 1.0) > 0:
                replacements.update(
                    {source.casefold(): target for source, target in context.substitutions}
                )
            if weights.get("preference", 1.0) > 0:
                for source, target in context.preferences:
                    replacements.setdefault(source.casefold(), target)
            glossary = Glossary(replacements=replacements, warnings=glossary.warnings)
            personalization_metadata = {
                "personalization_snapshot_version": context.snapshot_version,
                "personalization_consent_reference": context.consent_reference,
                "personalization_consent_version": context.consent_version,
                "personalization_substitution_count": len(context.substitutions),
                "personalization_preference_count": len(context.preferences),
                "personalization_weights": weights,
            }
        backend = (
            "rules"
            if policy.source == "hub_context" and policy.correction_policy == "rules"
            else self._config.postprocess_backend
            if policy.source == "runtime_default"
            else "none"
        )
        processor = build_postprocessor(backend, glossary=glossary)
        if processor is None:
            if glossary.warnings:
                return result.with_additional_warnings(list(glossary.warnings)), None
            return result, None
        inputs = tuple(segment.text for segment in result.segments) or (result.text,)
        processed_parts = tuple(processor.process(text) for text in inputs)
        segment_records = [
            {
                "segment_index": index if result.segments else None,
                "start_ms": result.segments[index].start_ms if result.segments else None,
                "end_ms": result.segments[index].end_ms if result.segments else None,
                "original_text": processed.original_text,
                "applied_text": processed.text,
                "proposed_text": processed.proposed_text,
                "review_required": processed.review_required,
                "conflict_reason": processed.conflict_reason,
                "edits": [edit.as_dict() for edit in processed.edits],
            }
            for index, processed in enumerate(processed_parts)
        ]
        processed_segments = tuple(
            replace(segment, text=processed_parts[index].text)
            for index, segment in enumerate(result.segments)
        )
        processed_text = (
            " ".join(segment.text for segment in processed_segments if segment.text).strip()
            if processed_segments
            else processed_parts[0].text
        )
        postprocess_trace = {
            "processor": processor.name(),
            "segments": segment_records,
            "review_required": any(item.review_required for item in processed_parts),
        }
        trace = {**dict(result.decision_trace), "postprocessing": postprocess_trace}
        warnings = tuple(
            dict.fromkeys([*result.warnings, *(warning for item in processed_parts for warning in item.warnings)])
        )
        updated = replace(
            result,
            text=processed_text,
            segments=processed_segments or result.segments,
            warnings=warnings,
            decision_trace=trace,
        )
        return updated, {
            "stage": "postprocess",
            "backend": processor.name(),
            "changed": any(processed.changed for processed in processed_parts),
            "review_required": postprocess_trace["review_required"],
            "segments": segment_records,
            "llm_used": processor.name() == "llm",
            **personalization_metadata,
            **glossary.as_stage_metadata(),
        }


def apply_hub_correction_boundary(
    result: TranscriptionResult,
    *,
    policy: VoiceExecutionPolicy,
) -> tuple[TranscriptionResult, dict | None]:
    if policy.correction_policy in {"restricted_choice", "local_schema_corrector"}:
        policy_name = "restricted_choice" if policy.correction_policy == "restricted_choice" else "generative_local"
        return result, {
            "stage": "correction_boundary",
            "policy": policy_name,
            "status": "hub_postprocessing_required",
            "runtime_worker_call": False,
            "no_generation": policy.correction_policy == "restricted_choice",
        }
    return result, None

