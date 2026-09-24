"""Hub policy for VisionDecision tasks (``VisionDecisionPolicy`` implementation).

The hub, not the caller, owns the schema: a request names a task from :data:`DEFAULT_VISION_TASK_CATALOG`,
and each task fixes its fields, the actions a value maps to and how uncertain fields escalate. The
VisionDecision provider only proposes a value; this policy decides what the hub may do with it. Rules
are evaluated in order and the first match wins; every decision names its rule:

======  ====================================================================  =========
Rule    Condition                                                             Verdict
======  ====================================================================  =========
V0      the proposal claims ``grants_permission``                             deny
V1      the schema id is not a task of the catalog                            deny
V2      the field is not part of the task                                     deny
V3      the value is not allowed by the task's field schema                   deny
V4      the mapped action type is not permitted for this policy instance      deny
V5      task status is unknown (not experimental/beta/stable)                 deny
V6      source ``human``: an explicit human answer is its own confirmation    allow
V7      source ``chat``: an escalation answer is unscored                     confirm
V8      model proposal without a probability                                  deny
V9      experimental task or confirm-only task/field                          confirm
V10     serving model is not a model the task was calibrated for              confirm
V11     probability below the field's ``allow_min_probability``               confirm
V12     the action needs a confirmation by design (state change)              confirm
V13     otherwise (calibrated, confident, informational or low-risk action)   allow
======  ====================================================================  =========

``allow`` lets the hub release an informational value or run a typed low-risk action; nothing in a
decision value is a permission by itself (``gate_vision_decision`` keeps ``grants_permission=False``
and turns policy exceptions into ``deny``).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from agent.services.vision_decision_hub_gate import EscalationTarget, VisionPolicyVerdict, VisionProposal
from agent.services.vision_decision_provider import VisionDecisionSchema, VisionField

KNOWN_TASK_STATUSES = frozenset({"experimental", "beta", "stable"})
PROPOSAL_SOURCES = frozenset({"model", "chat", "human"})
DEFAULT_ALLOW_MIN_PROBABILITY = 0.9


@dataclass(frozen=True)
class VisionTaskAction:
    """Typed hub action a decision value maps to."""

    value: Any
    action_type: str
    direct: bool = False  # False: always needs an explicit confirmation (V12)


@dataclass(frozen=True)
class VisionTaskField:
    spec: VisionField
    actions: Mapping[Any, VisionTaskAction] = field(default_factory=dict)  # empty: informational field
    confirm_only: bool = False
    human_review: bool = False  # uncertain values go to a human, never to a model
    allow_min_probability: float = DEFAULT_ALLOW_MIN_PROBABILITY

    @property
    def name(self) -> str:
        return self.spec.name

    def action_for(self, value: Any) -> VisionTaskAction | None:
        if isinstance(value, (dict, list)):
            return None
        try:
            action = self.actions.get(value)
        except TypeError:
            return None
        # 1 == True in Python: a value only matches an action value of exactly the same type.
        return action if action is not None and type(action.value) is type(value) else None


@dataclass(frozen=True)
class VisionTask:
    task_id: str
    status: str
    fields: Mapping[str, VisionTaskField]
    instructions: str = ""
    calibrated_models: frozenset[str] = frozenset()
    confirm_only: bool = False
    escalation_target: EscalationTarget = EscalationTarget.CHAT_COMPLETION

    @property
    def schema(self) -> VisionDecisionSchema:
        schema = VisionDecisionSchema(
            schema_id=self.task_id,
            fields=tuple(item.spec for item in self.fields.values()),
            instructions=self.instructions,
        )
        schema.validate()
        return schema

    @property
    def human_review_fields(self) -> tuple[str, ...]:
        return tuple(name for name, item in self.fields.items() if item.human_review)


def _task(task_id: str, spec: Mapping[str, Mapping[str, Any]], **kwargs: Any) -> VisionTask:
    """Build a task from the fork's compact schema form plus per-field hub metadata."""
    options = {name: dict(raw.get("hub") or {}) for name, raw in spec.items()}
    schema = VisionDecisionSchema.from_mapping(
        task_id, {name: {k: v for k, v in raw.items() if k != "hub"} for name, raw in spec.items()}
    )
    fields = {}
    for item in schema.fields:
        meta = options[item.name]
        actions = tuple(meta.pop("actions", ()))
        fields[item.name] = VisionTaskField(spec=item, actions={a.value: a for a in actions}, **meta)
    return VisionTask(task_id=task_id, fields=fields, **kwargs)


# shapes-v1 (beta): the fork's labelled shape set; Qwen3-VL-2B Q8_0 reaches >= 94 % per field on it
# (docs/vision-decision-llamacpp.md). Informational only: values are released, nothing runs.
_SHAPES = _task(
    "shapes-v1",
    {
        "shape": {"type": "enum", "choices": ["circle", "square", "triangle"], "description": "Which shape is drawn?"},
        "color": {"type": "enum", "choices": ["red", "green", "blue", "yellow"], "description": "What colour are the shapes?"},
        "count": {"type": "integer", "minimum": 1, "maximum": 4, "description": "How many shapes are there?"},
        # The model answers this with p=1.0 even on mid grey: never trust it without a human.
        "dark_background": {"type": "boolean", "description": "Is the background dark?", "hub": {"confirm_only": True}},
    },
    status="beta",
    calibrated_models=frozenset({"Qwen3-VL-2B-Instruct-Q8_0.gguf"}),
)

_ORIENTATIONS = ("upright", "rotated_90", "rotated_180", "rotated_270")
_DOCUMENT_KINDS = ("invoice", "receipt", "letter", "form", "photo", "other")

# document-intake-v1 (experimental, not measured on real scans): rotating a page is reversible and
# may run directly once calibrated; routing a document to a queue changes state and always needs a
# confirmation; whether a page shows personal data is a human call when the model is unsure.
_DOCUMENT_INTAKE = _task(
    "document-intake-v1",
    {
        "page_orientation": {
            "type": "enum",
            "choices": list(_ORIENTATIONS),
            "description": "How is the page rotated relative to upright reading orientation?",
            "hub": {
                "actions": [VisionTaskAction(o, f"vision.document.rotate.{o}", direct=True) for o in _ORIENTATIONS],
            },
        },
        "document_kind": {
            "type": "enum",
            "choices": list(_DOCUMENT_KINDS),
            "description": "What kind of document is shown?",
            "hub": {"actions": [VisionTaskAction(k, f"vision.document.route.{k}") for k in _DOCUMENT_KINDS]},
        },
        "contains_personal_data": {
            "type": "boolean",
            "description": "Does the page show personal data such as names, addresses or ID numbers?",
            "hub": {"confirm_only": True, "human_review": True},
        },
    },
    status="experimental",
    instructions="The image is a single scanned or photographed document page.",
)

DEFAULT_VISION_TASK_CATALOG: Mapping[str, VisionTask] = {item.task_id: item for item in (_SHAPES, _DOCUMENT_INTAKE)}


@dataclass(frozen=True)
class VisionTaskPolicyDecision:
    verdict: VisionPolicyVerdict
    rule: str
    action: VisionTaskAction | None = None


@dataclass(frozen=True)
class VisionTaskPolicy:
    """Deterministic rule table above; ``evaluate`` satisfies ``VisionDecisionPolicy``."""

    catalog: Mapping[str, VisionTask] = field(default_factory=lambda: DEFAULT_VISION_TASK_CATALOG)
    permitted_action_types: frozenset[str] | None = None  # None: every catalog action

    def evaluate(self, proposal: VisionProposal) -> VisionPolicyVerdict:
        return self.decide_proposal(proposal).verdict

    def task(self, task_id: str | None) -> VisionTask | None:
        return self.catalog.get(str(task_id))

    def decide_proposal(self, proposal: VisionProposal, *, source: str = "model") -> VisionTaskPolicyDecision:
        provenance = proposal.provenance if isinstance(proposal.provenance, Mapping) else {}
        return self.decide(
            task_id=proposal.schema_id,
            field_name=proposal.field,
            value=proposal.value,
            probability=proposal.probability,
            model=provenance.get("model"),
            source=source,
            grants_permission=proposal.grants_permission,
        )

    def decide(
        self,
        *,
        task_id: str | None,
        field_name: str,
        value: Any,
        probability: float | None,
        model: str | None,
        source: str = "model",
        grants_permission: Any = False,
    ) -> VisionTaskPolicyDecision:
        if grants_permission is not False:
            return VisionTaskPolicyDecision(VisionPolicyVerdict.DENY, "V0_permission_claim")
        task = self.task(task_id)
        if task is None:
            return VisionTaskPolicyDecision(VisionPolicyVerdict.DENY, "V1_unknown_task")
        task_field = task.fields.get(field_name)
        if task_field is None:
            return VisionTaskPolicyDecision(VisionPolicyVerdict.DENY, "V2_unknown_field")
        if not task_field.spec.allowed(value):
            return VisionTaskPolicyDecision(VisionPolicyVerdict.DENY, "V3_value_not_allowed")
        action = task_field.action_for(value)
        if task_field.actions and action is None:
            return VisionTaskPolicyDecision(VisionPolicyVerdict.DENY, "V3_value_not_allowed")
        if (
            action is not None
            and self.permitted_action_types is not None
            and action.action_type not in self.permitted_action_types
        ):
            return VisionTaskPolicyDecision(VisionPolicyVerdict.DENY, "V4_action_not_permitted", action)
        if task.status not in KNOWN_TASK_STATUSES or source not in PROPOSAL_SOURCES:
            return VisionTaskPolicyDecision(VisionPolicyVerdict.DENY, "V5_unknown_task_status", action)
        if source == "human":
            return VisionTaskPolicyDecision(VisionPolicyVerdict.ALLOW, "V6_human_answer", action)
        if source == "chat":
            return VisionTaskPolicyDecision(VisionPolicyVerdict.CONFIRM, "V7_unscored_escalation_answer", action)
        if (
            probability is None
            or isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(probability)
        ):
            return VisionTaskPolicyDecision(VisionPolicyVerdict.DENY, "V8_missing_probability", action)
        if task.status == "experimental" or task.confirm_only or task_field.confirm_only:
            return VisionTaskPolicyDecision(VisionPolicyVerdict.CONFIRM, "V9_confirm_only", action)
        if not _model_calibrated(model, task.calibrated_models):
            return VisionTaskPolicyDecision(VisionPolicyVerdict.CONFIRM, "V10_model_not_calibrated", action)
        if not probability >= task_field.allow_min_probability:
            return VisionTaskPolicyDecision(VisionPolicyVerdict.CONFIRM, "V11_low_probability", action)
        if action is not None and not action.direct:
            return VisionTaskPolicyDecision(VisionPolicyVerdict.CONFIRM, "V12_confirmation_required", action)
        return VisionTaskPolicyDecision(VisionPolicyVerdict.ALLOW, "V13_allowed", action)


def _model_calibrated(model: Any, calibrated: frozenset[str]) -> bool:
    if not isinstance(model, str) or not model:
        return False
    return model in calibrated or os.path.basename(model) in calibrated
