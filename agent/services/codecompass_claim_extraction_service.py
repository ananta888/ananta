"""Hub admission for claim proposals. Workers cannot persist claims."""

from __future__ import annotations

from typing import Any

from worker.knowledge_hygiene.claim_extraction_port import (
    FixtureClaimExtractor,
    LangExtractClaimExtractor,
    admit_claims,
)


class CodeCompassClaimExtractionService:
    def extract_fixture(self, source_text: str, *, source_ref: str, revision: str = "", allowed_source_refs: set[str] | None = None) -> dict[str, Any]:
        spans = FixtureClaimExtractor().extract(source_text)
        return admit_claims(source_text, spans, source_ref=source_ref, revision=revision, allowed_source_refs=allowed_source_refs)

    def extract_langextract(
        self,
        source_text: str,
        *,
        source_ref: str,
        revision: str,
        profile: str,
        allowed_source_refs: set[str],
        extractor=None,
    ) -> dict[str, Any]:
        spans = LangExtractClaimExtractor(extractor=extractor).extract(source_text, profile=profile)
        return admit_claims(
            source_text,
            spans,
            source_ref=source_ref,
            revision=revision,
            profile=profile,
            provider="langextract",
            allowed_source_refs=allowed_source_refs,
        )


_service = CodeCompassClaimExtractionService()


def get_codecompass_claim_extraction_service() -> CodeCompassClaimExtractionService:
    return _service
