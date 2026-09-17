"""Closed, dependency-free Specialist Decision Contract (GBMF-001).

A specialist model learns exactly one bounded decision. The contract fixes the
typed input, the closed label set, abstain/escalate behaviour and the optional
calibrated confidence. Free text is never the machine contract for a
safety-relevant decision: outputs are parsed into ``label`` (+ ``confidence``)
or rejected.

Label sources are kept apart so datasets and benchmarks can state *who* said
the label was right: a deterministic gate, a policy rule, a benchmark oracle
or a human reviewer.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

CONTRACT_SCHEMA = "ananta.specialist-decision-contract.v1"
OUTPUT_SCHEMA = "ananta.specialist-decision-output.v1"

LABEL_SOURCE_DETERMINISTIC_GATE = "deterministic_gate"
LABEL_SOURCE_POLICY_RULE = "policy_rule"
LABEL_SOURCE_BENCHMARK_ORACLE = "benchmark_oracle"
LABEL_SOURCE_HUMAN = "human"
LABEL_SOURCES = frozenset(
    {
        LABEL_SOURCE_DETERMINISTIC_GATE,
        LABEL_SOURCE_POLICY_RULE,
        LABEL_SOURCE_BENCHMARK_ORACLE,
        LABEL_SOURCE_HUMAN,
    }
)
# Which label sources count as reproducible ground truth for training.
REPRODUCIBLE_LABEL_SOURCES = frozenset(
    {LABEL_SOURCE_DETERMINISTIC_GATE, LABEL_SOURCE_POLICY_RULE, LABEL_SOURCE_BENCHMARK_ORACLE}
)

# Safety role of a label: a wrong "allow" (false allow) and a wrong "deny"
# (false deny) are reported separately and can carry their own hard limits.
SAFETY_ALLOW = "allow"
SAFETY_DENY = "deny"
SAFETY_NEUTRAL = "neutral"
SAFETY_ROLES = frozenset({SAFETY_ALLOW, SAFETY_DENY, SAFETY_NEUTRAL})

INPUT_TYPES = frozenset({"string", "integer", "number", "boolean", "string_list", "object"})

_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VERSION = re.compile(r"^v[0-9]{1,4}(?:\.[0-9]{1,4}){0,2}$")
_LABEL = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_FIELD = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_LABELS = 32
MAX_INPUT_FIELDS = 32
MAX_STRING_CHARS = 8_000
MAX_LIST_ITEMS = 256


class SpecialistContractError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InputField:
    name: str
    type: str
    required: bool = True
    max_chars: int = MAX_STRING_CHARS

    def __post_init__(self) -> None:
        if not _FIELD.fullmatch(self.name):
            raise SpecialistContractError("specialist_input_field_name_invalid")
        if self.type not in INPUT_TYPES:
            raise SpecialistContractError("specialist_input_field_type_invalid")
        if type(self.max_chars) is not int or not 1 <= self.max_chars <= MAX_STRING_CHARS:
            raise SpecialistContractError("specialist_input_field_bound_invalid")

    def validate(self, value: Any) -> Any:
        if isinstance(value, bool) and self.type != "boolean":
            raise SpecialistContractError("specialist_input_type_mismatch")
        if self.type == "string":
            if not isinstance(value, str) or len(value) > self.max_chars:
                raise SpecialistContractError("specialist_input_type_mismatch")
        elif self.type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise SpecialistContractError("specialist_input_type_mismatch")
        elif self.type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value != value:
                raise SpecialistContractError("specialist_input_type_mismatch")
        elif self.type == "boolean":
            if not isinstance(value, bool):
                raise SpecialistContractError("specialist_input_type_mismatch")
        elif self.type == "string_list":
            if (
                not isinstance(value, (list, tuple))
                or len(value) > MAX_LIST_ITEMS
                or any(not isinstance(item, str) or len(item) > self.max_chars for item in value)
            ):
                raise SpecialistContractError("specialist_input_type_mismatch")
            return list(value)
        elif self.type == "object":
            if not isinstance(value, Mapping) or len(json.dumps(value, default=str)) > self.max_chars:
                raise SpecialistContractError("specialist_input_type_mismatch")
            return dict(value)
        return value


@dataclass(frozen=True)
class ConfidencePolicy:
    """Optional calibrated confidence; never a substitute for correctness."""

    enabled: bool = False
    abstain_below: float | None = None  # confidence below which the model must abstain/escalate

    def __post_init__(self) -> None:
        if self.abstain_below is not None:
            if not self.enabled:
                raise SpecialistContractError("specialist_confidence_policy_invalid")
            if not isinstance(self.abstain_below, (int, float)) or not 0.0 <= float(self.abstain_below) <= 1.0:
                raise SpecialistContractError("specialist_confidence_policy_invalid")


@dataclass(frozen=True)
class SpecialistDecisionContract:
    specialist_id: str
    contract_version: str
    decision: str
    input_fields: tuple[InputField, ...]
    allowed_labels: tuple[str, ...]
    safety_roles: Mapping[str, str] = field(default_factory=dict)
    abstain_label: str | None = None
    escalate_label: str | None = None
    confidence: ConfidencePolicy = ConfidencePolicy()

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.specialist_id):
            raise SpecialistContractError("specialist_id_invalid")
        if not _VERSION.fullmatch(self.contract_version):
            raise SpecialistContractError("specialist_contract_version_invalid")
        if not isinstance(self.decision, str) or not 1 <= len(self.decision) <= 500:
            raise SpecialistContractError("specialist_decision_text_invalid")
        if not 1 <= len(self.input_fields) <= MAX_INPUT_FIELDS or len({f.name for f in self.input_fields}) != len(
            self.input_fields
        ):
            raise SpecialistContractError("specialist_input_fields_invalid")
        labels = tuple(self.allowed_labels)
        if not 2 <= len(labels) <= MAX_LABELS or len(set(labels)) != len(labels):
            raise SpecialistContractError("specialist_labels_invalid")
        for label in labels:
            if not _LABEL.fullmatch(label):
                raise SpecialistContractError("specialist_labels_invalid")
        for label, role in dict(self.safety_roles).items():
            if label not in labels or role not in SAFETY_ROLES:
                raise SpecialistContractError("specialist_safety_roles_invalid")
        for special in (self.abstain_label, self.escalate_label):
            if special is not None and special not in labels:
                raise SpecialistContractError("specialist_special_label_invalid")
        if self.confidence.abstain_below is not None and self.abstain_label is None and self.escalate_label is None:
            raise SpecialistContractError("specialist_confidence_policy_invalid")

    # -- identity -----------------------------------------------------------

    @property
    def contract_key(self) -> str:
        return f"{self.specialist_id}@{self.contract_version}"

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": CONTRACT_SCHEMA,
            "specialist_id": self.specialist_id,
            "contract_version": self.contract_version,
            "decision": self.decision,
            "input_schema": [
                {"name": f.name, "type": f.type, "required": f.required, "max_chars": f.max_chars}
                for f in self.input_fields
            ],
            "output_schema": {
                "schema": OUTPUT_SCHEMA,
                "label": {"enum": list(self.allowed_labels)},
                "confidence": {"enabled": self.confidence.enabled, "abstain_below": self.confidence.abstain_below},
            },
            "allowed_labels": list(self.allowed_labels),
            "safety_roles": {label: self.safety_roles.get(label, SAFETY_NEUTRAL) for label in self.allowed_labels},
            "abstain_label": self.abstain_label,
            "escalate_label": self.escalate_label,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_mapping())

    # -- validation ---------------------------------------------------------

    def safety_role(self, label: str) -> str:
        return self.safety_roles.get(label, SAFETY_NEUTRAL)

    @property
    def deferral_labels(self) -> frozenset[str]:
        return frozenset(label for label in (self.abstain_label, self.escalate_label) if label is not None)

    def validate_input(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise SpecialistContractError("specialist_input_object_required")
        known = {f.name: f for f in self.input_fields}
        unknown = sorted(set(map(str, value)) - set(known))
        if unknown:
            raise SpecialistContractError("specialist_input_unknown_field")
        normalized: dict[str, Any] = {}
        for name, spec in known.items():
            if name not in value:
                if spec.required:
                    raise SpecialistContractError("specialist_input_field_missing")
                continue
            normalized[name] = spec.validate(value[name])
        return normalized

    def validate_label(self, label: Any) -> str:
        if not isinstance(label, str) or label not in self.allowed_labels:
            raise SpecialistContractError("specialist_label_not_allowed")
        return label

    def parse_output(self, output: Any) -> dict[str, Any]:
        """Parse a model output (JSON text or mapping) into the typed decision."""
        if isinstance(output, str):
            if not 1 <= len(output.encode("utf-8")) <= 4_096:
                raise SpecialistContractError("specialist_output_size_invalid")
            try:
                output = json.loads(output)
            except ValueError as exc:
                raise SpecialistContractError("specialist_output_json_invalid") from exc
        if not isinstance(output, Mapping):
            raise SpecialistContractError("specialist_output_object_required")
        allowed_keys = {"schema", "label", "confidence"}
        if set(map(str, output)) - allowed_keys:
            raise SpecialistContractError("specialist_output_unknown_field")
        if output.get("schema") not in (None, OUTPUT_SCHEMA):
            raise SpecialistContractError("specialist_output_schema_invalid")
        label = self.validate_label(output.get("label"))
        confidence = output.get("confidence")
        if confidence is not None:
            if not self.confidence.enabled:
                raise SpecialistContractError("specialist_confidence_not_enabled")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
                raise SpecialistContractError("specialist_confidence_invalid")
            confidence = float(confidence)
        elif self.confidence.enabled:
            raise SpecialistContractError("specialist_confidence_required")
        return {"schema": OUTPUT_SCHEMA, "label": label, "confidence": confidence}

    def apply_abstention(self, decision: Mapping[str, Any], *, threshold: float | None = None) -> dict[str, Any]:
        """Replace a low-confidence label with the abstain/escalate label."""
        limit = self.confidence.abstain_below if threshold is None else threshold
        result = dict(decision)
        deferral = self.escalate_label or self.abstain_label
        if limit is None or deferral is None or result.get("confidence") is None:
            return result
        if result["label"] not in self.deferral_labels and float(result["confidence"]) < float(limit):
            result["label"] = deferral
            result["deferred"] = True
        return result


def _contract(specialist_id, decision, fields, labels, *, safety=None, abstain=None, escalate=None, confidence=True):
    return SpecialistDecisionContract(
        specialist_id=specialist_id,
        contract_version="v1",
        decision=decision,
        input_fields=tuple(InputField(name, kind) for name, kind in fields),
        allowed_labels=tuple(labels),
        safety_roles=dict(safety or {}),
        abstain_label=abstain,
        escalate_label=escalate,
        confidence=ConfidencePolicy(enabled=confidence, abstain_below=0.6 if confidence and (abstain or escalate) else None),
    )


def builtin_contracts() -> dict[str, SpecialistDecisionContract]:
    """Initial specialist candidates from the track; all share one contract type."""
    contracts = [
        _contract(
            "tool-router",
            "Which allowed tool (or no tool) should be used for the next step?",
            (("step_goal", "string"), ("allowed_tools", "string_list"), ("recent_observations", "string")),
            ("tool:codecompass.retrieve", "tool:run_tests", "tool:read_file", "tool:apply_patch", "no_tool", "escalate"),
            escalate="escalate",
        ),
        _contract(
            "code-risk-gate",
            "Is a proposed code/tool action allowed within the defined risk classes?",
            (("action_kind", "string"), ("target_paths", "string_list"), ("diff_summary", "string"), ("policy_profile", "string")),
            ("allow", "deny", "escalate"),
            safety={"allow": SAFETY_ALLOW, "deny": SAFETY_DENY, "escalate": SAFETY_NEUTRAL},
            escalate="escalate",
        ),
        _contract(
            "plan-validator",
            "Is the next planned step consistent with goal, current state and policies?",
            (("goal", "string"), ("current_state", "string"), ("next_step", "string"), ("policies", "string_list")),
            ("valid", "repair", "escalate"),
            safety={"valid": SAFETY_ALLOW, "repair": SAFETY_DENY},
            escalate="escalate",
        ),
        _contract(
            "structured-output-repair",
            "Which minimal typed repair brings a known output into the expected contract?",
            (("expected_schema", "string"), ("raw_output", "string"), ("parser_error", "string")),
            ("repair:strip_markdown_fence", "repair:close_json", "repair:coerce_types", "repair:none_needed", "escalate"),
            escalate="escalate",
        ),
        _contract(
            "retrieval-relevance",
            "Is a chunk relevant enough for the downstream context of a concrete question?",
            (("question", "string"), ("chunk", "string")),
            ("relevant", "not_relevant"),
        ),
        _contract(
            "task-completion-gate",
            "Is the task actually complete according to explicit acceptance criteria?",
            (("acceptance_criteria", "string_list"), ("evidence", "string"), ("failed_checks", "string_list")),
            ("complete", "incomplete", "escalate"),
            safety={"complete": SAFETY_ALLOW, "incomplete": SAFETY_DENY},
            escalate="escalate",
        ),
    ]
    return {contract.specialist_id: contract for contract in contracts}


__all__ = [
    "CONTRACT_SCHEMA",
    "OUTPUT_SCHEMA",
    "LABEL_SOURCES",
    "LABEL_SOURCE_BENCHMARK_ORACLE",
    "LABEL_SOURCE_DETERMINISTIC_GATE",
    "LABEL_SOURCE_HUMAN",
    "LABEL_SOURCE_POLICY_RULE",
    "REPRODUCIBLE_LABEL_SOURCES",
    "SAFETY_ALLOW",
    "SAFETY_DENY",
    "SAFETY_NEUTRAL",
    "ConfidencePolicy",
    "InputField",
    "SpecialistContractError",
    "SpecialistDecisionContract",
    "builtin_contracts",
    "canonical_digest",
]
