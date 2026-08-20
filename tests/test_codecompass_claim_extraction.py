from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from worker.knowledge_hygiene.claim_extraction_port import (
    ClaimSpan,
    FixtureClaimExtractor,
    admit_claims,
)


def test_fixture_extractor_grounds_spans() -> None:
    source = "Ananta uses CodeCompass as its context layer."
    spans = FixtureClaimExtractor().extract(source)
    result = admit_claims(source, spans, source_ref="docs/codecompass.md", revision="rev-1")
    schema = json.loads(Path("schemas/codecompass.claim-extraction.v1.json").read_text())
    jsonschema.validate(result, schema)
    assert result["status"] == "ok"
    assert result["claims"][0]["grounded"] is True
    assert result["source_verification_status"] == "unverified"


def test_ungrounded_span_is_rejected() -> None:
    source = "hello world"
    fake = ClaimSpan(text="not present", span_start=0, span_end=3)
    result = admit_claims(source, [fake], source_ref="x")
    assert result["status"] == "empty"
    assert result["claims"] == []


def test_unknown_source_identifier_is_rejected() -> None:
    source = "CodeCompass"
    spans = FixtureClaimExtractor().extract(source)
    result = admit_claims(source, spans, source_ref="SRC_UNKNOWN", allowed_source_refs={"SRC_ALLOWED"})
    assert result["status"] == "rejected"
    assert result["source_verification_status"] == "failed"
