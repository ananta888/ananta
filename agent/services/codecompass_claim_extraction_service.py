"""Hub admission for claim proposals. Workers cannot persist claims."""

from __future__ import annotations

from typing import Any

from worker.knowledge_hygiene.claim_extraction_port import (
    FixtureClaimExtractor,
    admit_claims,
)


class CodeCompassClaimExtractionService:
    def extract_fixture(self, source_text: str, *, source_ref: str, revision: str = "") -> dict[str, Any]:
        spans = FixtureClaimExtractor().extract(source_text)
        return admit_claims(source_text, spans, source_ref=source_ref, revision=revision)


_service = CodeCompassClaimExtractionService()


def get_codecompass_claim_extraction_service() -> CodeCompassClaimExtractionService:
    return _service
