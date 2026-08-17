"""Provider-neutral claim extraction. LangExtract stays behind this port."""

from __future__ import annotations

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

    def extract(self, source_text: str, *, profile: str = "ollama") -> list[ClaimSpan]:
        try:
            import langextract  # type: ignore
        except ImportError as exc:
            raise RuntimeError("langextract_not_installed") from exc
        raise RuntimeError("langextract_requires_explicit_hub_profile")


def admit_claims(
    source_text: str,
    spans: list[ClaimSpan],
    *,
    source_ref: str,
    revision: str = "",
    profile: str = "fixture",
    provider: str = "fixture",
) -> dict[str, Any]:
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
        "revision": revision,
        "profile": profile,
        "provider": provider,
        "claims": accepted,
    }
