"""Hub admission for claim proposals. Workers cannot persist claims."""

from __future__ import annotations

from typing import Any

from worker.knowledge_hygiene.claim_extraction_port import (
    FixtureClaimExtractor,
    LangExtractClaimExtractor,
    admit_claims,
)


class CodeCompassClaimExtractionService:
    def extract_fixture(
        self,
        source_text: str,
        *,
        source_ref: str,
        revision: str = "",
        allowed_source_refs: set[str] | None = None,
        run_ref: str = "",
        content_digest: str = "",
        allowed_run_refs: set[str] | None = None,
        allowed_revisions: set[str] | None = None,
    ) -> dict[str, Any]:
        spans = FixtureClaimExtractor().extract(source_text)
        return admit_claims(
            source_text,
            spans,
            source_ref=source_ref,
            revision=revision,
            allowed_source_refs=allowed_source_refs,
            run_ref=run_ref,
            content_digest=content_digest,
            allowed_run_refs=allowed_run_refs,
            allowed_revisions=allowed_revisions,
        )

    def extract_langextract(
        self,
        source_text: str,
        *,
        source_ref: str,
        revision: str,
        profile: str,
        allowed_source_refs: set[str],
        extractor=None,
        run_ref: str = "",
        content_digest: str = "",
        allowed_run_refs: set[str] | None = None,
        allowed_revisions: set[str] | None = None,
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
            run_ref=run_ref,
            content_digest=content_digest,
            allowed_run_refs=allowed_run_refs,
            allowed_revisions=allowed_revisions,
        )


_service = CodeCompassClaimExtractionService()


def get_codecompass_claim_extraction_service() -> CodeCompassClaimExtractionService:
    return _service
