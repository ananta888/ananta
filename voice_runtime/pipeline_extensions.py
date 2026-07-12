from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .preprocessing.audio_decode import DecodedPcmAudio
from .preprocessing.audio_enhancement import (
    AudioEnhancementProcessor,
    BypassProcessor,
    ChannelMixProcessor,
    DcOffsetRemovalProcessor,
    DeterministicAudioEnhancementPipeline,
    HighPassProcessor,
    LimiterProcessor,
    PeakNormalizationProcessor,
    ResampleProcessor,
)


@dataclass(frozen=True)
class PreparedAudioVariant:
    profile: str
    variant_id: str
    content: bytes
    metadata: dict[str, object]
    duplicate_of: str | None = None


def prepare_enhancement_variants(
    audio: DecodedPcmAudio,
    profiles: tuple[str, ...],
) -> tuple[PreparedAudioVariant, ...]:
    enhancer = DeterministicAudioEnhancementPipeline()
    original = enhancer.original_variant(audio)
    variants = [
        PreparedAudioVariant(
            profile="original",
            variant_id=original.variant_id,
            content=audio.to_wav_bytes(),
            metadata=original.as_metadata(),
        )
    ]
    digest_owner = {_content_digest(variants[0].content): original.variant_id}
    for profile in profiles:
        if profile == "original":
            continue
        enhanced = enhancer.run(original, _processors(profile, audio), label=profile)
        content = enhanced.audio.to_wav_bytes()
        digest = _content_digest(content)
        duplicate_of = digest_owner.get(digest)
        if duplicate_of is None:
            digest_owner[digest] = enhanced.variant_id
        metadata = enhanced.as_metadata()
        if duplicate_of:
            metadata = {**metadata, "duplicate_of": duplicate_of, "candidate_execution": "skipped_duplicate_pcm"}
        variants.append(
            PreparedAudioVariant(
                profile=profile,
                variant_id=enhanced.variant_id,
                content=content,
                metadata=metadata,
                duplicate_of=duplicate_of,
            )
        )
    return tuple(variants)


def _processors(profile: str, audio: DecodedPcmAudio) -> tuple[AudioEnhancementProcessor, ...]:
    if profile == "bypass":
        return (BypassProcessor(),)
    if profile == "normalized":
        return (
            ChannelMixProcessor(),
            DcOffsetRemovalProcessor(),
            PeakNormalizationProcessor(target_peak=0.92, max_gain=4.0),
            LimiterProcessor(threshold=0.98),
            ResampleProcessor(target_sample_rate_hz=16_000),
        )
    if profile == "high_pass":
        return (
            ChannelMixProcessor(),
            HighPassProcessor(cutoff_hz=80.0),
            LimiterProcessor(threshold=0.98),
            ResampleProcessor(target_sample_rate_hz=16_000),
        )
    if profile == "speech_safe":
        return (
            ChannelMixProcessor(),
            DcOffsetRemovalProcessor(),
            HighPassProcessor(cutoff_hz=80.0),
            PeakNormalizationProcessor(target_peak=0.92, max_gain=3.0),
            LimiterProcessor(threshold=0.98),
            ResampleProcessor(target_sample_rate_hz=16_000),
        )
    raise ValueError(f"unsupported audio enhancement profile: {profile}")


def _content_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
