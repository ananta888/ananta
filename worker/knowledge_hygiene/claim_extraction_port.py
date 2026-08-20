"""Provider-neutral claim extraction. LangExtract stays behind this port."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ClaimSpan:
    text: str
    span_start: int
    span_end: int
    label: str = "claim"

    def grounded_in(self, source: str) -> bool:
        if self.span_start < 0 or self.span_end > len(source) or self.span_end <= self.span_start:
            return False
        return source[self.span_start : self.span_end] == self.text


class ClaimExtractionPort(Protocol):
    name: str

    def extract(self, source_text: str, *, profile: str = "fixture") -> list[ClaimSpan]: ...


class FixtureClaimExtractor:
    """Deterministic offline adapter for CI. No network, no LLM."""

    name = "fixture"

    def extract(self, source_text: str, *, profile: str = "fixture") -> list[ClaimSpan]:
        text = str(source_text or "")
        claims: list[ClaimSpan] = []
        needle = "CodeCompass"
        start = text.find(needle)
        if start >= 0:
            claims.append(ClaimSpan(text=needle, span_start=start, span_end=start + len(needle), label="system"))
        return claims


class LangExtractClaimExtractor:
    """Optional google-langextract adapter. Untrusted until Hub admission."""

    name = "langextract"

    def __init__(self, extractor: Any | None = None) -> None:
        self._extractor = extractor

    def extract(self, source_text: str, *, profile: str = "ollama") -> list[ClaimSpan]:
        try:
            import langextract  # type: ignore
        except ImportError as exc:
            raise RuntimeError("langextract_not_installed") from exc
        extractor = self._extractor or getattr(langextract, "extract", None)
        if not callable(extractor):
            raise RuntimeError("langextract_adapter_unavailable")
        result = extractor(text_or_documents=str(source_text or ""), model_id=profile)
        raw_items = getattr(result, "extractions", result if isinstance(result, list) else [])
        spans: list[ClaimSpan] = []
        for item in raw_items:
            text = str(getattr(item, "extraction_text", "") or getattr(item, "text", ""))
            interval = getattr(item, "char_interval", None)
            start = getattr(interval, "start_pos", getattr(item, "span_start", -1))
            end = getattr(interval, "end_pos", getattr(item, "span_end", -1))
            label = str(getattr(item, "extraction_class", "claim") or "claim")
            try:
                spans.append(ClaimSpan(text=text, span_start=int(start), span_end=int(end), label=label))
            except (TypeError, ValueError):
                continue
        return spans


def admit_claims(
    source_text: str,
    spans: list[ClaimSpan],
    *,
    source_ref: str,
    revision: str = "",
    profile: str = "fixture",
    provider: str = "fixture",
    allowed_source_refs: set[str] | None = None,
    run_ref: str = "",
    content_digest: str = "",
    allowed_run_refs: set[str] | None = None,
    allowed_revisions: set[str] | None = None,
) -> dict[str, Any]:
    source_token = str(source_ref or "").strip()
    run_token = str(run_ref or "").strip()
    actual_digest = "sha256:" + hashlib.sha256(str(source_text).encode("utf-8")).hexdigest()
    supplied_digest = str(content_digest or "").strip()
    identifier_like = source_token.startswith(("SRC_", "RUN_"))
    source_verified = bool(
        identifier_like
        and allowed_source_refs is not None
        and source_token in allowed_source_refs
    )
    if identifier_like and not source_verified:
        return {
            "schema": "codecompass.claim-extraction.v1",
            "status": "rejected",
            "reason": "unknown_source_reference",
            "source_ref": source_token,
            "source_verification_status": "failed",
            "revision": revision,
            "profile": profile,
            "provider": provider,
            "claims": [],
        }
    rejection_reason = ""
    if run_token.startswith("RUN_") and (
        allowed_run_refs is None or run_token not in allowed_run_refs
    ):
        rejection_reason = "unknown_run_reference"
    elif allowed_revisions is not None and str(revision) not in allowed_revisions:
        rejection_reason = "revision_not_allowed"
    elif supplied_digest and supplied_digest != actual_digest:
        rejection_reason = "content_digest_mismatch"
    if rejection_reason:
        return {
            "schema": "codecompass.claim-extraction.v1",
            "status": "rejected",
            "reason": rejection_reason,
            "source_ref": source_token,
            "source_verification_status": "verified" if source_verified else "unverified",
            "run_ref": run_token,
            "run_verification_status": "failed" if run_token else "not_provided",
            "revision": revision,
            "content_digest": actual_digest,
            "profile": profile,
            "provider": provider,
            "claims": [],
        }
    accepted = []
    for span in spans:
        grounded = span.grounded_in(source_text)
        if not grounded:
            continue
        accepted.append(
            {
                "text": span.text,
                "span_start": span.span_start,
                "span_end": span.span_end,
                "grounded": True,
                "label": span.label,
            }
        )
    return {
        "schema": "codecompass.claim-extraction.v1",
        "status": "ok" if accepted else "empty",
        "reason": "" if accepted else "no_grounded_claims",
        "source_ref": source_ref,
        "source_verification_status": "verified" if source_verified else "unverified",
        "run_ref": run_token,
        "run_verification_status": (
            "verified"
            if run_token and allowed_run_refs is not None and run_token in allowed_run_refs
            else "not_provided"
        ),
        "revision": revision,
        "content_digest": actual_digest,
        "profile": profile,
        "provider": provider,
        "claims": accepted,
    }
