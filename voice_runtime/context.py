from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .execution_policy import HubVoiceConfiguration


@dataclass(frozen=True)
class LowConfidenceRegion:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class VoiceRecognitionContext:
    classic_transcript: str = ""
    classic_words: tuple[Mapping[str, Any], ...] = ()
    low_confidence_regions: tuple[LowConfidenceRegion, ...] = ()
    glossary_terms: tuple[str, ...] = ()
    user_vocabulary: tuple[str, ...] = ()
    substitutions: tuple[tuple[str, str], ...] = ()
    preferences: tuple[tuple[str, str], ...] = ()
    personalization_weights: tuple[tuple[str, float], ...] = ()
    language_hint: str | None = None
    domain_hint: str | None = None
    previous_segment_context: str = ""
    snapshot_version: str | None = None
    consent_reference: str | None = None
    consent_version: int | None = None
    snapshot_expires_at: float | None = None
    configuration: HubVoiceConfiguration | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "VoiceRecognitionContext":
        if not raw:
            return cls()
        if not isinstance(raw, Mapping):
            raise ValueError("voice recognition context must be an object")
        _reject_instruction_keys(raw)
        personalization = raw.get("personalization")
        snapshot = personalization if isinstance(personalization, Mapping) else {}
        if snapshot:
            if snapshot.get("runtime_persistence_allowed") is not False or snapshot.get("persistence_owner") != "hub":
                raise ValueError("voice personalization snapshot has an invalid ownership contract")
            try:
                expires_at = float(snapshot.get("expires_at") or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError("voice personalization snapshot expiry is invalid") from exc
            if expires_at <= time.time():
                raise ValueError("voice personalization snapshot is expired")
            if snapshot.get("consent_granted") is not True:
                raise ValueError("voice personalization snapshot consent is revoked")
            consent_id = str(snapshot.get("consent_id") or "").strip()
            consent_version = snapshot.get("consent_version")
            revocation_epoch = snapshot.get("revocation_epoch")
            if not consent_id or not isinstance(consent_version, int) or isinstance(consent_version, bool):
                raise ValueError("voice personalization snapshot consent reference is invalid")
            if consent_version < 1:
                raise ValueError("voice personalization snapshot consent version is invalid")
            if revocation_epoch != consent_version:
                raise ValueError("voice personalization snapshot revocation epoch is invalid")
        raw_vocabulary = snapshot.get("vocabulary")
        vocabulary = [str(item) for item in raw_vocabulary] if isinstance(raw_vocabulary, list) else []
        substitutions = _bounded_pairs(snapshot.get("substitutions"), max_pairs=256, max_chars=8_000)
        preferences = _bounded_pairs(snapshot.get("preferences"), max_pairs=256, max_chars=8_000)
        weights = _bounded_weights(snapshot.get("weights"))
        raw_glossary = raw.get("glossary_terms")
        glossary = [str(item) for item in raw_glossary] if isinstance(raw_glossary, list) else []
        classic_transcript = _bounded_text(str(raw.get("classic_transcript") or ""), 12_000)
        classic_words = _bounded_words(raw.get("classic_words"), max_words=2_000)
        low_confidence_regions = _bounded_regions(raw.get("low_confidence_regions"), max_regions=256)
        raw_configuration = raw.get("configuration")
        if "configuration" in raw and not isinstance(raw_configuration, Mapping):
            raise ValueError("voice execution configuration must be an object")
        return cls(
            classic_transcript=classic_transcript,
            classic_words=classic_words,
            low_confidence_regions=low_confidence_regions,
            glossary_terms=tuple(_bounded_terms(glossary, max_terms=256, max_chars=8_000)),
            user_vocabulary=tuple(_bounded_terms(vocabulary, max_terms=256, max_chars=8_000)),
            substitutions=substitutions,
            preferences=preferences,
            personalization_weights=weights,
            language_hint=_optional_bounded_text(raw.get("language_hint"), 32),
            domain_hint=_optional_bounded_text(raw.get("domain_hint"), 128),
            previous_segment_context=_bounded_text(str(raw.get("previous_segment_context") or ""), 2_000),
            snapshot_version=str(snapshot.get("version") or "") or None,
            consent_reference=str(snapshot.get("consent_id") or "") or None,
            consent_version=int(snapshot["consent_version"]) if snapshot else None,
            snapshot_expires_at=float(snapshot["expires_at"]) if snapshot else None,
            configuration=HubVoiceConfiguration.from_mapping(raw_configuration)
            if isinstance(raw_configuration, Mapping)
            else None,
        )

    def project(self, capabilities: Iterable[str], *, max_chars: int = 8_000, max_terms: int = 256) -> dict[str, Any]:
        """Project only explicitly supported fields into an adapter request.

        Transcript data always remains data; this method never creates prompt or
        instruction fields and therefore cannot widen execution policy.
        """

        allowed = frozenset(str(item) for item in capabilities)
        result: dict[str, Any] = {}
        if "transcript_reference" in allowed and self.classic_transcript:
            result["classic_transcript"] = _bounded_text(self.classic_transcript, max_chars)
        if "word_reference" in allowed and self.classic_words:
            result["classic_words"] = [dict(item) for item in self.classic_words[:max_terms]]
        if "low_confidence_regions" in allowed and self.low_confidence_regions:
            result["low_confidence_regions"] = [item.as_dict() for item in self.low_confidence_regions[:max_terms]]
        if "hotwords" in allowed:
            terms = _bounded_terms(
                (*self.glossary_terms, *self.user_vocabulary), max_terms=max_terms, max_chars=max_chars
            )
            if terms:
                result["hotwords"] = terms
        if "language_hint" in allowed and self.language_hint:
            result["language_hint"] = _bounded_text(self.language_hint, 32)
        if "domain_hint" in allowed and self.domain_hint:
            result["domain_hint"] = _bounded_text(self.domain_hint, 128)
        if "previous_segment_context" in allowed and self.previous_segment_context:
            result["previous_segment_context"] = _bounded_text(self.previous_segment_context, max_chars)
        return result


def _bounded_text(value: str, max_chars: int) -> str:
    return str(value).replace("\x00", "")[: max(0, int(max_chars))]


def _bounded_terms(values: Iterable[str], *, max_terms: int, max_chars: int) -> list[str]:
    result: list[str] = []
    used = 0
    for raw in values:
        value = " ".join(str(raw).replace("\x00", "").split())
        if not value or value in result:
            continue
        if len(result) >= max_terms or used + len(value) > max_chars:
            break
        result.append(value)
        used += len(value)
    return result


def _optional_bounded_text(value: object, max_chars: int) -> str | None:
    normalized = _bounded_text(str(value or ""), max_chars).strip()
    return normalized or None


def _bounded_pairs(raw: object, *, max_pairs: int, max_chars: int) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("voice personalization pairs must be an array")
    result: list[tuple[str, str]] = []
    used = 0
    for item in raw[:max_pairs]:
        if not isinstance(item, Mapping) or set(item) - {"source", "target"}:
            raise ValueError("voice personalization pair has an invalid shape")
        source = " ".join(str(item.get("source") or "").replace("\x00", "").split())
        target = " ".join(str(item.get("target") or "").replace("\x00", "").split())
        if not source or not target:
            raise ValueError("voice personalization pair must contain source and target")
        if used + len(source) + len(target) > max_chars:
            break
        pair = (source, target)
        if pair not in result:
            result.append(pair)
            used += len(source) + len(target)
    return tuple(result)


def _bounded_weights(raw: object) -> tuple[tuple[str, float], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Mapping) or set(raw) - {"preference", "substitution", "vocabulary"}:
        raise ValueError("voice personalization weights have an invalid shape")
    result: list[tuple[str, float]] = []
    for name in sorted(raw):
        value = raw[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
            raise ValueError("voice personalization weight must be between zero and one")
        result.append((str(name), float(value)))
    return tuple(result)


def _bounded_words(raw: object, *, max_words: int) -> tuple[Mapping[str, Any], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("classic_words must be an array")
    result: list[Mapping[str, Any]] = []
    for item in raw[:max_words]:
        if not isinstance(item, Mapping):
            raise ValueError("classic_words entries must be objects")
        text = _bounded_text(str(item.get("text") or ""), 256).strip()
        start_ms = item.get("start_ms")
        end_ms = item.get("end_ms")
        if not text or not isinstance(start_ms, int) or not isinstance(end_ms, int) or not 0 <= start_ms <= end_ms:
            raise ValueError("classic_words entry is invalid")
        result.append({"text": text, "start_ms": start_ms, "end_ms": end_ms})
    return tuple(result)


def _bounded_regions(raw: object, *, max_regions: int) -> tuple[LowConfidenceRegion, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("low_confidence_regions must be an array")
    result: list[LowConfidenceRegion] = []
    for item in raw[:max_regions]:
        if not isinstance(item, Mapping):
            raise ValueError("low_confidence region must be an object")
        start_ms = item.get("start_ms")
        end_ms = item.get("end_ms")
        confidence = item.get("confidence")
        if not isinstance(start_ms, int) or not isinstance(end_ms, int) or not 0 <= start_ms <= end_ms:
            raise ValueError("low_confidence region timeline is invalid")
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= float(confidence) <= 1
        ):
            raise ValueError("low_confidence region confidence is invalid")
        result.append(
            LowConfidenceRegion(
                start_ms=start_ms,
                end_ms=end_ms,
                text=_bounded_text(str(item.get("text") or ""), 2_000),
                confidence=float(confidence) if confidence is not None else None,
            )
        )
    return tuple(result)


def _reject_instruction_keys(raw: Mapping[str, Any], *, depth: int = 0) -> None:
    if depth > 4:
        raise ValueError("voice recognition context nesting is too deep")
    forbidden = {"prompt", "instruction", "system", "messages", "tools", "role", "command"}
    for key, value in raw.items():
        if str(key).casefold() in forbidden:
            raise ValueError("voice recognition context contains a forbidden instruction field")
        if isinstance(value, Mapping):
            _reject_instruction_keys(value, depth=depth + 1)
