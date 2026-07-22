import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent.services.sfu_broadcast_contract_validator import (
    ContractDefinition,
    CorpusVersion,
    ExpiryRule,
    ReceiverGroupMemberDigestRule,
    SfuBroadcastContractValidator,
    SfuBroadcastCorpusVerifier,
    StructuralLimits,
    ValidationContext,
    ValidationPhase,
)


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class RecordingTrustStore:
    def __init__(self, trusted: bool) -> None:
        self.trusted = trusted
        self.calls: list[str] = []

    def verify(self, contract_id, document) -> bool:
        self.calls.append(contract_id)
        return self.trusted


class RepositoryArtifacts:
    def __init__(self, root: Path, replacements=None) -> None:
        self.root = root.resolve()
        self.replacements = replacements or {}

    def read_bytes(self, relative_path: str) -> bytes:
        if relative_path in self.replacements:
            return self.replacements[relative_path]
        candidate = (self.root / relative_path).resolve()
        candidate.relative_to(self.root)
        return candidate.read_bytes()


def _schema():
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://ananta.local/tests/sfu-boundary.v1.json",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema", "scope", "route_epoch", "sequence", "expires_at_ms", "items"],
        "properties": {
            "schema": {"const": "test.sfu-boundary.v1"},
            "scope": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tenant", "room"],
                "properties": {"tenant": {"type": "string"}, "room": {"type": "string"}},
            },
            "route_epoch": {"type": "integer", "minimum": 1},
            "sequence": {"type": "integer", "minimum": 1},
            "expires_at_ms": {"type": "integer", "minimum": 1},
            "items": {"type": "array", "maxItems": 16, "items": {"type": "integer"}},
        },
    }


def _definition(*, signature_required=True, semantic_rules=()):
    return ContractDefinition(
        contract_id="test.sfu-boundary.v1",
        schema_version="1",
        schema=_schema(),
        expiry=ExpiryRule("epoch_ms", "/expires_at_ms"),
        sequence_pointer="/sequence",
        epoch_pointers={"route_epoch": "/route_epoch"},
        scope_pointers={"tenant": "/scope/tenant", "room": "/scope/room"},
        semantic_rules=semantic_rules,
        signature_required=signature_required,
    )


def _payload():
    return {
        "schema": "test.sfu-boundary.v1",
        "scope": {"tenant": "tenant-a", "room": "room-a"},
        "route_epoch": 4,
        "sequence": 41,
        "expires_at_ms": 1_900_000_010_000,
        "items": [1],
    }


def _context(**changes):
    values = {
        "expected_scope": {"tenant": "tenant-a", "room": "room-a"},
        "latest_epochs": {"route_epoch": 4},
        "accepted_sequences": frozenset(),
        "last_sequence": 40,
    }
    values.update(changes)
    return ValidationContext(**values)


def _validator(*, trusted=True, limits=None, clock=None, definition=None):
    trust = RecordingTrustStore(trusted)
    validator = SfuBroadcastContractValidator(
        definitions=[definition or _definition()],
        clock=clock or FixedClock(datetime.fromtimestamp(1_900_000_001, tz=timezone.utc)),
        trust_store=trust,
        limits=limits
        or StructuralLimits(
            max_document_bytes=2048,
            max_depth=8,
            max_nodes=64,
            max_collection_items=16,
            max_string_bytes=256,
            max_total_string_bytes=1024,
        ),
    )
    return validator, trust


def _raw(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_minimum_and_exact_byte_maximum_are_accepted():
    validator, trust = _validator()
    minimal = _raw(_payload())
    maximum = minimal + b" " * (2048 - len(minimal))

    assert validator.validate("test.sfu-boundary.v1", minimal, _context()).reason_code == "ok"
    assert validator.validate("test.sfu-boundary.v1", maximum, _context()).reason_code == "ok"
    assert trust.calls == ["test.sfu-boundary.v1", "test.sfu-boundary.v1"]


@pytest.mark.parametrize(
    ("case", "expected_phase", "expected_code"),
    [
        ("unknown", ValidationPhase.SCHEMA, "contract_unknown_property"),
        ("oversize", ValidationPhase.STRUCTURAL, "contract_document_bytes_exceeded"),
        ("expiry", ValidationPhase.SEMANTICS, "contract_expired"),
        ("replay", ValidationPhase.SEMANTICS, "contract_replay"),
        ("stale_epoch", ValidationPhase.SEMANTICS, "contract_stale_epoch"),
        ("cross_scope", ValidationPhase.SEMANTICS, "contract_cross_scope"),
        ("signature", ValidationPhase.SIGNATURE, "contract_signature_invalid"),
    ],
)
def test_negative_corpus_categories_have_stable_reason_codes(case, expected_phase, expected_code):
    payload = _payload()
    context = _context()
    trusted = True
    clock = None
    if case == "unknown":
        payload["unknown"] = True
    elif case == "oversize":
        payload["items"] = [1]
    elif case == "expiry":
        clock = FixedClock(datetime.fromtimestamp(1_900_000_010, tz=timezone.utc))
    elif case == "replay":
        context = _context(last_sequence=41)
    elif case == "stale_epoch":
        context = _context(latest_epochs={"route_epoch": 5})
    elif case == "cross_scope":
        context = _context(expected_scope={"tenant": "tenant-a", "room": "room-b"})
    elif case == "signature":
        trusted = False

    validator, trust = _validator(trusted=trusted, clock=clock)
    raw = _raw(payload)
    if case == "oversize":
        raw += b" " * (2049 - len(raw))
    result = validator.validate("test.sfu-boundary.v1", raw, context)

    assert result.valid is False
    assert result.phase is expected_phase
    assert result.reason_code == expected_code
    assert len(trust.calls) == (1 if case == "signature" else 0)


def test_structural_then_schema_then_semantics_then_signature_order_is_fail_closed():
    payload = _payload()
    payload["unknown"] = True
    validator, trust = _validator(trusted=False)
    oversize = _raw(payload) + b" " * 2048
    assert validator.validate("test.sfu-boundary.v1", oversize, _context()).phase is ValidationPhase.STRUCTURAL
    assert trust.calls == []

    payload["expires_at_ms"] = 1
    assert validator.validate("test.sfu-boundary.v1", _raw(payload), _context()).phase is ValidationPhase.SCHEMA
    assert trust.calls == []

    payload.pop("unknown")
    assert validator.validate("test.sfu-boundary.v1", _raw(payload), _context()).phase is ValidationPhase.SEMANTICS
    assert trust.calls == []

    payload["expires_at_ms"] = 1_900_000_010_000
    assert validator.validate("test.sfu-boundary.v1", _raw(payload), _context()).phase is ValidationPhase.SIGNATURE
    assert trust.calls == ["test.sfu-boundary.v1"]


def test_depth_count_and_non_finite_tokens_fail_before_schema_or_trust():
    tight = StructuralLimits(
        max_document_bytes=2048,
        max_depth=4,
        max_nodes=32,
        max_collection_items=4,
        max_string_bytes=256,
        max_total_string_bytes=1024,
    )
    validator, trust = _validator(limits=tight)
    deep = b'{"nested":[[[[[0]]]]]}'
    wide = b'{"items":[1,2,3,4,5]}'
    non_finite = _raw(_payload()).replace(b'"items":[1]', b'"items":[NaN]')

    assert validator.validate("test.sfu-boundary.v1", deep, _context()).reason_code == "contract_depth_exceeded"
    assert validator.validate("test.sfu-boundary.v1", wide, _context()).reason_code == "contract_collection_count_exceeded"
    assert validator.validate("test.sfu-boundary.v1", non_finite, _context()).reason_code == "contract_json_invalid"
    assert trust.calls == []


def test_missing_schema_reference_fails_closed_without_resolution_or_network():
    schema = _schema()
    schema["properties"]["scope"] = {"$ref": "https://unavailable.invalid/scope.v1.json"}
    definition = ContractDefinition("test.sfu-boundary.v1", "1", schema, signature_required=False)
    validator, trust = _validator(definition=definition)

    result = validator.validate("test.sfu-boundary.v1", _raw(_payload()), ValidationContext())

    assert result.reason_code == "contract_schema_unavailable"
    assert trust.calls == []


def test_con002_bad_jcs_hmac_is_recorded_as_a_real_fail_closed_mismatch():
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads(
        (root / "tests/fixtures/webrtc/receiver_group_intent/valid_group.v1.json").read_text(encoding="utf-8")
    )
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://ananta.local/tests/receiver-group-digest-only.v1.json",
        "type": "object",
    }
    definition = ContractDefinition(
        "ananta.webrtc.receiver-group-intent.v1",
        "1",
        schema,
        semantic_rules=(ReceiverGroupMemberDigestRule(),),
        signature_required=False,
    )
    validator = SfuBroadcastContractValidator(
        definitions=[definition],
        clock=FixedClock(datetime.fromtimestamp(1_900_000_001, tz=timezone.utc)),
        trust_store=RecordingTrustStore(True),
        limits=StructuralLimits(),
    )

    result = validator.validate(
        definition.contract_id,
        _raw(fixture["instance"]),
        ValidationContext(metadata=fixture["validation_context"]),
    )

    assert result.valid is False
    assert result.phase is ValidationPhase.SEMANTICS
    assert result.reason_code == "receiver_group_member_digest_mismatch"


@pytest.fixture(scope="module")
def corpus_manifest():
    root = Path(__file__).resolve().parents[1]
    return json.loads((root / "tests/contracts/sfu_broadcast/corpus.v1.json").read_text(encoding="utf-8"))


def test_shared_corpus_covers_every_required_case_and_reports_version_digest(corpus_manifest):
    root = Path(__file__).resolve().parents[1]
    report = SfuBroadcastCorpusVerifier().inspect(corpus_manifest, RepositoryArtifacts(root))

    assert report.integrity_valid is True
    assert report.contract_count == 10
    assert report.fixture_count == 90
    assert report.version.schema_version == 1
    assert report.version.corpus_version == 1
    assert report.version.corpus_digest.startswith("sha256:")
    assert len(report.version.corpus_digest) == 71
    assert len(report.fail_closed_blockers) == 6
    assert report.release_eligible is False


def test_manifest_or_fixture_drift_without_version_pin_change_breaks_gate(corpus_manifest):
    root = Path(__file__).resolve().parents[1]
    verifier = SfuBroadcastCorpusVerifier()
    baseline = verifier.inspect(corpus_manifest, RepositoryArtifacts(root))
    pin = CorpusVersion(
        baseline.version.schema_version,
        baseline.version.corpus_version,
        baseline.version.corpus_digest,
    )

    changed_manifest = copy.deepcopy(corpus_manifest)
    changed_manifest["contracts"][1]["cases"]["signature"]["expected_code"] = "changed_without_version"
    manifest_drift = verifier.inspect(changed_manifest, RepositoryArtifacts(root), expected_version=pin)
    assert "corpus_digest_mismatch" in {issue.code for issue in manifest_drift.integrity_issues}

    fixture_path = corpus_manifest["contracts"][1]["base_fixture"]
    fixture_drift = verifier.inspect(
        corpus_manifest,
        RepositoryArtifacts(root, {fixture_path: b"drift"}),
        expected_version=pin,
    )
    assert "corpus_digest_mismatch" in {issue.code for issue in fixture_drift.integrity_issues}
