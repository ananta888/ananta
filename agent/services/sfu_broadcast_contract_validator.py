"""Bounded, dependency-injected validation for SFU broadcast contracts.

The Hub owns this boundary.  Callers inject schemas, time, trust and current
Hub state; validation never performs network I/O, discovers global state or
delegates orchestration to a worker.  The fixed order is structural limits,
JSON Schema, Hub semantics and finally authentication.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


JsonMapping = Mapping[str, Any]


class ValidationPhase(str, Enum):
    STRUCTURAL = "structural"
    SCHEMA = "schema"
    SEMANTICS = "semantics"
    SIGNATURE = "signature"
    ACCEPTED = "accepted"


@dataclass(frozen=True, slots=True)
class StructuralLimits:
    """Hard pre-schema limits; effective policy may only lower them."""

    max_document_bytes: int = 524_288
    max_depth: int = 32
    max_nodes: int = 8_192
    max_collection_items: int = 512
    max_string_bytes: int = 466_064
    max_total_string_bytes: int = 524_288

    def __post_init__(self) -> None:
        for value in (
            self.max_document_bytes,
            self.max_depth,
            self.max_nodes,
            self.max_collection_items,
            self.max_string_bytes,
            self.max_total_string_bytes,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("contract_limit_invalid")


@dataclass(frozen=True, slots=True)
class ExpiryRule:
    """Declarative expiry extraction without contract-specific branching."""

    mode: str
    value_pointer: str
    ttl_pointer: str | None = None
    fixed_ttl_ms: int | None = None


class SemanticRule(Protocol):
    def evaluate(self, document: JsonMapping, context: "ValidationContext") -> str | None:
        """Return one stable rejection code, or ``None`` when accepted."""


@dataclass(frozen=True, slots=True)
class ContractDefinition:
    contract_id: str
    schema_version: str
    schema: JsonMapping
    expiry: ExpiryRule | None = None
    sequence_pointer: str | None = None
    epoch_pointers: Mapping[str, str] = field(default_factory=dict)
    scope_pointers: Mapping[str, str] = field(default_factory=dict)
    semantic_rules: tuple[SemanticRule, ...] = ()
    signature_required: bool = True


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """Authoritative Hub state for one validation attempt."""

    expected_scope: Mapping[str, Any] = field(default_factory=dict)
    latest_epochs: Mapping[str, int] = field(default_factory=dict)
    accepted_sequences: frozenset[int] = frozenset()
    last_sequence: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class Clock(Protocol):
    def now(self) -> datetime:
        """Return an aware UTC-compatible timestamp."""


class TrustStore(Protocol):
    def verify(self, contract_id: str, document: JsonMapping) -> bool:
        """Authenticate the already schema- and semantics-valid document."""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    phase: ValidationPhase
    reason_code: str
    contract_id: str
    schema_version: str
    detail: str | None = None


class SfuBroadcastContractValidator:
    """Fail-closed validator for Hub-side broadcast contract boundaries."""

    def __init__(
        self,
        *,
        definitions: Sequence[ContractDefinition],
        clock: Clock,
        trust_store: TrustStore,
        limits: StructuralLimits,
        registry_resources: Mapping[str, JsonMapping] | None = None,
    ) -> None:
        self._clock = clock
        self._trust_store = trust_store
        self._limits = limits
        self._definitions = {definition.contract_id: definition for definition in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("contract_definition_duplicate")

        resources: dict[str, JsonMapping] = dict(registry_resources or {})
        for definition in definitions:
            schema_id = definition.schema.get("$id")
            if isinstance(schema_id, str):
                resources[schema_id] = definition.schema
        registry = Registry().with_resources(
            (resource_id, Resource.from_contents(document))
            for resource_id, document in resources.items()
        )
        self._validators = {
            definition.contract_id: Draft202012Validator(
                definition.schema,
                registry=registry,
                format_checker=FormatChecker(),
            )
            for definition in definitions
        }

    def validate(
        self,
        contract_id: str,
        raw_document: bytes | str,
        context: ValidationContext,
    ) -> ValidationResult:
        definition = self._definitions.get(contract_id)
        if definition is None:
            return ValidationResult(
                False,
                ValidationPhase.SCHEMA,
                "contract_definition_unavailable",
                contract_id,
                "unknown",
            )

        document, structural_failure = self._decode_bounded(raw_document)
        if structural_failure is not None:
            return self._rejection(definition, ValidationPhase.STRUCTURAL, structural_failure)
        assert document is not None

        try:
            errors = sorted(
                self._validators[contract_id].iter_errors(document),
                key=lambda error: (
                    tuple(str(component) for component in error.absolute_path),
                    str(error.validator),
                    error.message,
                ),
            )
        except Exception:
            return self._rejection(definition, ValidationPhase.SCHEMA, "contract_schema_unavailable")
        if errors:
            first = errors[0]
            reason = (
                "contract_unknown_property"
                if first.validator in {"additionalProperties", "unevaluatedProperties"}
                else "contract_schema_invalid"
            )
            pointer = "/" + "/".join(str(component) for component in first.absolute_path)
            return self._rejection(definition, ValidationPhase.SCHEMA, reason, pointer)

        semantic_failure = self._validate_semantics(definition, document, context)
        if semantic_failure is not None:
            return self._rejection(definition, ValidationPhase.SEMANTICS, semantic_failure)

        if definition.signature_required:
            try:
                trusted = self._trust_store.verify(contract_id, document)
            except Exception:
                trusted = False
            if trusted is not True:
                return self._rejection(
                    definition,
                    ValidationPhase.SIGNATURE,
                    "contract_signature_invalid",
                )

        return ValidationResult(
            True,
            ValidationPhase.ACCEPTED,
            "ok",
            definition.contract_id,
            definition.schema_version,
        )

    def _decode_bounded(self, raw_document: bytes | str) -> tuple[JsonMapping | None, str | None]:
        if isinstance(raw_document, str):
            raw = raw_document.encode("utf-8")
        elif isinstance(raw_document, bytes):
            raw = raw_document
        else:
            return None, "contract_input_type_invalid"
        if len(raw) > self._limits.max_document_bytes:
            return None, "contract_document_bytes_exceeded"

        scan_failure = self._scan_depth_and_strings(raw)
        if scan_failure is not None:
            return None, scan_failure
        try:
            text = raw.decode("utf-8", errors="strict")
            value = json.loads(text, parse_constant=_reject_non_finite)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            return None, "contract_json_invalid"
        if not isinstance(value, Mapping):
            return None, "contract_root_object_required"

        count_failure = self._check_decoded_limits(value)
        if count_failure is not None:
            return None, count_failure
        return value, None

    def _scan_depth_and_strings(self, raw: bytes) -> str | None:
        depth = 0
        in_string = False
        escaped = False
        string_bytes = 0
        total_string_bytes = 0
        for byte in raw:
            if in_string:
                if escaped:
                    escaped = False
                    string_bytes += 1
                    continue
                if byte == 0x5C:
                    escaped = True
                    string_bytes += 1
                    continue
                if byte == 0x22:
                    in_string = False
                    total_string_bytes += string_bytes
                    if string_bytes > self._limits.max_string_bytes:
                        return "contract_string_bytes_exceeded"
                    if total_string_bytes > self._limits.max_total_string_bytes:
                        return "contract_total_string_bytes_exceeded"
                    string_bytes = 0
                    continue
                string_bytes += 1
            elif byte == 0x22:
                in_string = True
            elif byte in (0x7B, 0x5B):
                depth += 1
                if depth > self._limits.max_depth:
                    return "contract_depth_exceeded"
            elif byte in (0x7D, 0x5D):
                depth -= 1
                if depth < 0:
                    return "contract_json_invalid"
        return None

    def _check_decoded_limits(self, document: JsonMapping) -> str | None:
        stack: list[tuple[Any, int]] = [(document, 1)]
        nodes = 0
        total_string_bytes = 0
        while stack:
            value, depth = stack.pop()
            nodes += 1
            if nodes > self._limits.max_nodes:
                return "contract_node_count_exceeded"
            if depth > self._limits.max_depth:
                return "contract_depth_exceeded"
            if isinstance(value, Mapping):
                if len(value) > self._limits.max_collection_items:
                    return "contract_collection_count_exceeded"
                for key, child in value.items():
                    if not isinstance(key, str):
                        return "contract_json_invalid"
                    key_bytes = len(key.encode("utf-8"))
                    if key_bytes > self._limits.max_string_bytes:
                        return "contract_string_bytes_exceeded"
                    total_string_bytes += key_bytes
                    stack.append((child, depth + 1))
            elif isinstance(value, list):
                if len(value) > self._limits.max_collection_items:
                    return "contract_collection_count_exceeded"
                stack.extend((child, depth + 1) for child in value)
            elif isinstance(value, str):
                value_bytes = len(value.encode("utf-8"))
                if value_bytes > self._limits.max_string_bytes:
                    return "contract_string_bytes_exceeded"
                total_string_bytes += value_bytes
            if total_string_bytes > self._limits.max_total_string_bytes:
                return "contract_total_string_bytes_exceeded"
        return None

    def _validate_semantics(
        self,
        definition: ContractDefinition,
        document: JsonMapping,
        context: ValidationContext,
    ) -> str | None:
        if definition.expiry is not None:
            expiry_ms = _resolve_expiry_ms(document, definition.expiry)
            now = self._clock.now()
            if expiry_ms is None or now.tzinfo is None:
                return "contract_expiry_unavailable"
            if int(now.timestamp() * 1000) >= expiry_ms:
                return "contract_expired"

        if definition.sequence_pointer is not None:
            sequence = _resolve_pointer(document, definition.sequence_pointer)
            if type(sequence) is not int:
                return "contract_sequence_unavailable"
            if sequence in context.accepted_sequences:
                return "contract_replay"
            if context.last_sequence is not None and sequence <= context.last_sequence:
                return "contract_replay"

        for epoch_name, pointer in definition.epoch_pointers.items():
            if epoch_name not in context.latest_epochs:
                return "contract_epoch_context_unavailable"
            candidate = _resolve_pointer(document, pointer)
            current = context.latest_epochs[epoch_name]
            if type(candidate) is not int or type(current) is not int:
                return "contract_epoch_context_unavailable"
            if candidate < current:
                return "contract_stale_epoch"
            if candidate > current:
                return "contract_epoch_mismatch"

        for scope_name, pointer in definition.scope_pointers.items():
            if scope_name not in context.expected_scope:
                return "contract_scope_context_unavailable"
            if _resolve_pointer(document, pointer) != context.expected_scope[scope_name]:
                return "contract_cross_scope"

        for rule in definition.semantic_rules:
            try:
                failure = rule.evaluate(document, context)
            except Exception:
                return "contract_semantic_rule_failed_closed"
            if failure is not None:
                return failure
        return None

    @staticmethod
    def _rejection(
        definition: ContractDefinition,
        phase: ValidationPhase,
        reason: str,
        detail: str | None = None,
    ) -> ValidationResult:
        return ValidationResult(
            False,
            phase,
            reason,
            definition.contract_id,
            definition.schema_version,
            detail,
        )


class ReceiverGroupMemberDigestRule:
    """Rebuild the RFC-8785-compatible integer/string-only member HMAC input."""

    _DOMAIN = b"ananta.webrtc.receiver-group.member-digest.v1"

    def evaluate(self, document: JsonMapping, context: ValidationContext) -> str | None:
        state = context.metadata
        audience = state.get("resolved_audience")
        keys = state.get("test_only_hmac_keys_hex")
        if not isinstance(audience, Mapping) or not isinstance(keys, Mapping):
            return "receiver_group_digest_context_unavailable"
        members = audience.get("members")
        if not isinstance(members, list):
            return "receiver_group_digest_context_unavailable"

        publications = document.get("publications")
        scope = document.get("scope")
        epochs = document.get("epochs")
        policy = document.get("policy")
        if not all(isinstance(value, Mapping) for value in (publications, scope, epochs, policy)):
            return "receiver_group_digest_context_unavailable"
        assert isinstance(publications, Mapping)
        assert isinstance(scope, Mapping)
        assert isinstance(epochs, Mapping)
        assert isinstance(policy, Mapping)

        primary = publications.get("primary_publication_ref")
        shared = publications.get("shared_publication_refs")
        digests = publications.get("member_digests")
        if not isinstance(primary, str) or not isinstance(shared, list) or not isinstance(digests, list):
            return "receiver_group_digest_context_unavailable"
        allowed_publications = sorted([primary, *shared], key=lambda value: str(value).encode("utf-8"))

        for digest in digests:
            if not isinstance(digest, Mapping):
                return "receiver_group_digest_context_unavailable"
            publication_ref = digest.get("publication_ref")
            key_ref = digest.get("key_ref")
            supplied = digest.get("value")
            key_hex = keys.get(key_ref) if isinstance(key_ref, str) else None
            if not all(isinstance(value, str) for value in (publication_ref, key_ref, supplied, key_hex)):
                return "receiver_group_digest_context_unavailable"

            bindings: list[dict[str, str]] = []
            for member in members:
                if not isinstance(member, Mapping):
                    return "receiver_group_digest_context_unavailable"
                grants = member.get("publication_grants")
                if not isinstance(grants, list):
                    return "receiver_group_digest_context_unavailable"
                matching = [grant for grant in grants if grant.get("publication_ref") == publication_ref]
                if len(matching) != 1:
                    return "receiver_group_digest_context_unavailable"
                grant = matching[0]
                binding = {
                    "grant_ref": grant.get("grant_ref"),
                    "member_ref": member.get("member_ref"),
                    "subscription_ref": grant.get("subscription_ref"),
                }
                if not all(isinstance(value, str) for value in binding.values()):
                    return "receiver_group_digest_context_unavailable"
                bindings.append(binding)  # type: ignore[arg-type]
            bindings.sort(
                key=lambda binding: (
                    binding["member_ref"].encode("utf-8"),
                    binding["grant_ref"].encode("utf-8"),
                    binding["subscription_ref"].encode("utf-8"),
                )
            )
            digest_input = {
                "allowed_publication_refs": allowed_publications,
                "audience_epoch": epochs.get("audience_epoch"),
                "audience_snapshot_ref": document.get("audience_snapshot_ref"),
                "consent_epoch": epochs.get("consent_epoch"),
                "consent_scope_ref": scope.get("consent_scope_ref"),
                "group_id": document.get("group_id"),
                "key_epoch": epochs.get("key_epoch"),
                "member_bindings": bindings,
                "membership_epoch": epochs.get("membership_epoch"),
                "policy_ref": policy.get("policy_ref"),
                "policy_version": policy.get("version"),
                "privacy_scope": scope.get("privacy_scope"),
                "publication_ref": publication_ref,
                "publication_scope_ref": scope.get("publication_scope_ref"),
                "room_ref": scope.get("room_ref"),
                "tenant_ref": scope.get("tenant_ref"),
            }
            try:
                canonical = _canonical_json(digest_input)
                expected = hmac.new(
                    bytes.fromhex(key_hex),
                    self._DOMAIN + b"\x00" + canonical,
                    hashlib.sha256,
                ).hexdigest()
            except (TypeError, ValueError):
                return "receiver_group_digest_context_unavailable"
            if not hmac.compare_digest(expected, supplied):
                return "receiver_group_member_digest_mismatch"
        return None


class CorpusArtifactSource(Protocol):
    def read_bytes(self, relative_path: str) -> bytes:
        """Read a repository artifact without network access."""


@dataclass(frozen=True, slots=True)
class CorpusVersion:
    schema_version: int
    corpus_version: int
    corpus_digest: str


@dataclass(frozen=True, slots=True)
class CorpusIssue:
    code: str
    artifact: str | None = None


@dataclass(frozen=True, slots=True)
class CorpusReport:
    version: CorpusVersion
    contract_count: int
    fixture_count: int
    integrity_issues: tuple[CorpusIssue, ...]
    fail_closed_blockers: tuple[str, ...]

    @property
    def integrity_valid(self) -> bool:
        return not self.integrity_issues

    @property
    def release_eligible(self) -> bool:
        return self.integrity_valid and not self.fail_closed_blockers


class SfuBroadcastCorpusVerifier:
    """Bind a version to the catalog plus every referenced local artifact."""

    REQUIRED_CATEGORIES = frozenset(
        {
            "minimal",
            "maximum",
            "unknown_property",
            "oversize",
            "expiry",
            "replay",
            "stale_epoch",
            "cross_scope",
            "signature",
        }
    )
    SUPPORTED_PROBES = frozenset(
        {
            "source",
            "source_with_member_digest_check",
            "pad_to_document_limit",
            "add_unknown_property",
            "exceed_document_bytes",
            "advance_clock_past_expiry",
            "reuse_sequence",
            "raise_authoritative_epoch",
            "replace_expected_scope",
            "reject_trust",
        }
    )

    def inspect(
        self,
        manifest: JsonMapping,
        source: CorpusArtifactSource,
        *,
        expected_version: CorpusVersion | None = None,
    ) -> CorpusReport:
        issues: list[CorpusIssue] = []
        schema_version = manifest.get("schema_version")
        corpus_version = manifest.get("corpus_version")
        if type(schema_version) is not int or type(corpus_version) is not int:
            schema_version = 0
            corpus_version = 0
            issues.append(CorpusIssue("corpus_version_invalid"))

        declared_categories = manifest.get("required_categories")
        if not isinstance(declared_categories, list) or set(declared_categories) != self.REQUIRED_CATEGORIES:
            issues.append(CorpusIssue("corpus_categories_invalid"))

        contracts = manifest.get("contracts")
        if not isinstance(contracts, list):
            contracts = []
            issues.append(CorpusIssue("corpus_contracts_invalid"))
        fixture_count = 0
        artifact_paths: set[str] = set()
        contract_ids: set[str] = set()
        for contract in contracts:
            if not isinstance(contract, Mapping):
                issues.append(CorpusIssue("corpus_contract_invalid"))
                continue
            contract_id = contract.get("contract_id")
            if not isinstance(contract_id, str) or contract_id in contract_ids:
                issues.append(CorpusIssue("corpus_contract_id_invalid"))
            else:
                contract_ids.add(contract_id)
            for artifact_key in ("schema_path", "base_fixture"):
                artifact = contract.get(artifact_key)
                if isinstance(artifact, str):
                    artifact_paths.add(artifact)
                else:
                    issues.append(CorpusIssue("corpus_artifact_path_invalid"))
            cases = contract.get("cases")
            if not isinstance(cases, Mapping) or set(cases) != self.REQUIRED_CATEGORIES:
                issues.append(CorpusIssue("corpus_case_coverage_incomplete", str(contract_id)))
                continue
            fixture_count += len(cases)
            for case in cases.values():
                if not isinstance(case, Mapping):
                    issues.append(CorpusIssue("corpus_case_invalid", str(contract_id)))
                    continue
                if case.get("probe") not in self.SUPPORTED_PROBES:
                    issues.append(CorpusIssue("corpus_probe_invalid", str(contract_id)))
                if not isinstance(case.get("expected_code"), str):
                    issues.append(CorpusIssue("corpus_expectation_missing", str(contract_id)))
                fixture = case.get("fixture")
                if isinstance(fixture, str):
                    artifact_paths.add(fixture)

        blocker_ids: list[str] = []
        blockers = manifest.get("known_blockers")
        if not isinstance(blockers, list):
            issues.append(CorpusIssue("corpus_blockers_invalid"))
            blockers = []
        for blocker in blockers:
            if not isinstance(blocker, Mapping) or blocker.get("disposition") != "expected_fail_closed":
                issues.append(CorpusIssue("corpus_blocker_invalid"))
                continue
            blocker_id = blocker.get("id")
            artifact = blocker.get("fixture")
            if not isinstance(blocker_id, str) or not isinstance(artifact, str):
                issues.append(CorpusIssue("corpus_blocker_invalid"))
                continue
            blocker_ids.append(blocker_id)
            artifact_paths.add(artifact)

        digest = hashlib.sha256(_canonical_json(manifest))
        for artifact in sorted(artifact_paths):
            digest.update(b"\x00")
            digest.update(artifact.encode("utf-8"))
            digest.update(b"\x00")
            try:
                artifact_bytes = source.read_bytes(artifact)
            except Exception:
                issues.append(CorpusIssue("corpus_artifact_unavailable", artifact))
                digest.update(b"unavailable")
            else:
                digest.update(hashlib.sha256(artifact_bytes).digest())
        version = CorpusVersion(
            int(schema_version),
            int(corpus_version),
            "sha256:" + digest.hexdigest(),
        )
        if expected_version is not None:
            if version.schema_version != expected_version.schema_version:
                issues.append(CorpusIssue("corpus_schema_version_mismatch"))
            if version.corpus_version != expected_version.corpus_version:
                issues.append(CorpusIssue("corpus_version_mismatch"))
            if version.corpus_digest != expected_version.corpus_digest:
                issues.append(CorpusIssue("corpus_digest_mismatch"))

        return CorpusReport(
            version=version,
            contract_count=len(contracts),
            fixture_count=fixture_count,
            integrity_issues=tuple(issues),
            fail_closed_blockers=tuple(blocker_ids),
        )


def _reject_non_finite(value: str) -> None:
    raise ValueError(value)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _resolve_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        return None
    current = document
    for encoded in pointer[1:].split("/"):
        component = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if component not in current:
                return None
            current = current[component]
        elif isinstance(current, list) and component.isdigit():
            index = int(component)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _resolve_expiry_ms(document: JsonMapping, rule: ExpiryRule) -> int | None:
    value = _resolve_pointer(document, rule.value_pointer)
    if rule.mode == "epoch_ms":
        return value if type(value) is int else None
    if rule.mode == "iso8601":
        parsed = _parse_utc(value)
        return int(parsed.timestamp() * 1000) if parsed is not None else None
    if rule.mode in {"issued_plus_ttl_ms", "issued_plus_ttl_seconds"}:
        issued = _parse_utc(value) if isinstance(value, str) else None
        if issued is None:
            return None
        ttl = _resolve_pointer(document, rule.ttl_pointer or "")
        if type(ttl) is not int:
            return None
        multiplier = 1 if rule.mode == "issued_plus_ttl_ms" else 1000
        return int(issued.timestamp() * 1000) + ttl * multiplier
    if rule.mode == "issued_plus_fixed_ms":
        issued = _parse_utc(value) if isinstance(value, str) else None
        if issued is None or type(rule.fixed_ttl_ms) is not int:
            return None
        return int(issued.timestamp() * 1000) + rule.fixed_ttl_ms
    return None


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)
