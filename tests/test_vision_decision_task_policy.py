"""VisionTaskPolicy rule table and the escalation executor (chat completion, larger model, human tickets)."""
from __future__ import annotations

import json
import socket

import pytest

from agent.services.audio_decision_command_executor import VoiceCommandConfirmationStore
from agent.services.vision_decision_escalation_executor import (
    MAX_CHAT_FIELDS,
    ChatCompletionEscalationHandler,
    ChatTargetConfig,
    VisionEscalationConfig,
    VisionEscalationExecutor,
    build_escalation_executor,
    escalation_action_type,
)
from agent.services.vision_decision_hub_gate import EscalationTarget, VisionEscalation, VisionPolicyVerdict, VisionProposal
from agent.services.vision_decision_provider import VisionDecisionConfigurationError, VisionField
from agent.services.vision_decision_task_policy import (
    DEFAULT_VISION_TASK_CATALOG,
    VisionTask,
    VisionTaskField,
    VisionTaskPolicy,
)
from agent.services.voice_governance_domain import VoicePrincipal
from tests.test_vision_decision_provider import KEY, _config, _png

MODEL = "Qwen3-VL-2B-Instruct-Q8_0.gguf"
ALICE = VoicePrincipal(tenant_id="tenant-a", subject="alice")
SHAPES = DEFAULT_VISION_TASK_CATALOG["shapes-v1"]
DOCS = DEFAULT_VISION_TASK_CATALOG["document-intake-v1"]


def _decide(policy=None, **kwargs):
    values = {"task_id": "shapes-v1", "field_name": "shape", "value": "circle", "probability": 0.99, "model": MODEL}
    values.update(kwargs)
    return (policy or VisionTaskPolicy()).decide(**values)


# --- policy rule table ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "verdict", "rule"),
    [
        ({"grants_permission": True}, VisionPolicyVerdict.DENY, "V0_permission_claim"),
        ({"grants_permission": None}, VisionPolicyVerdict.DENY, "V0_permission_claim"),
        ({"task_id": "unknown-task"}, VisionPolicyVerdict.DENY, "V1_unknown_task"),
        ({"task_id": None}, VisionPolicyVerdict.DENY, "V1_unknown_task"),
        ({"field_name": "secret"}, VisionPolicyVerdict.DENY, "V2_unknown_field"),
        ({"value": "hexagon"}, VisionPolicyVerdict.DENY, "V3_value_not_allowed"),
        ({"field_name": "count", "value": True}, VisionPolicyVerdict.DENY, "V3_value_not_allowed"),
        ({"field_name": "count", "value": 9}, VisionPolicyVerdict.DENY, "V3_value_not_allowed"),
        ({"source": "plugin"}, VisionPolicyVerdict.DENY, "V5_unknown_task_status"),
        ({"source": "human", "probability": None}, VisionPolicyVerdict.ALLOW, "V6_human_answer"),
        ({"source": "chat", "probability": None}, VisionPolicyVerdict.CONFIRM, "V7_unscored_escalation_answer"),
        ({"probability": None}, VisionPolicyVerdict.DENY, "V8_missing_probability"),
        ({"probability": float("nan")}, VisionPolicyVerdict.DENY, "V8_missing_probability"),
        ({"field_name": "dark_background", "value": False}, VisionPolicyVerdict.CONFIRM, "V9_confirm_only"),
        ({"model": "SmolVLM-500M-Instruct-Q8_0.gguf"}, VisionPolicyVerdict.CONFIRM, "V10_model_not_calibrated"),
        ({"model": None}, VisionPolicyVerdict.CONFIRM, "V10_model_not_calibrated"),
        ({"probability": 0.85}, VisionPolicyVerdict.CONFIRM, "V11_low_probability"),
        ({}, VisionPolicyVerdict.ALLOW, "V13_allowed"),
        ({"model": "/models/vlm/" + MODEL}, VisionPolicyVerdict.ALLOW, "V13_allowed"),
    ],
)
def test_shapes_rule_table(kwargs, verdict, rule):
    decision = _decide(**kwargs)
    assert (decision.verdict, decision.rule) == (verdict, rule)


def test_experimental_document_task_always_confirms_model_values():
    decision = _decide(task_id="document-intake-v1", field_name="page_orientation", value="rotated_90")
    assert (decision.verdict, decision.rule) == (VisionPolicyVerdict.CONFIRM, "V9_confirm_only")
    assert decision.action.action_type == "vision.document.rotate.rotated_90" and decision.action.direct


def test_action_rules_v4_and_v12():
    stable_docs = VisionTask(
        task_id="docs-stable",
        status="stable",
        fields=DOCS.fields,
        calibrated_models=frozenset({MODEL}),
    )
    policy = VisionTaskPolicy(catalog={"docs-stable": stable_docs})
    route = _decide(policy, task_id="docs-stable", field_name="document_kind", value="invoice")
    assert (route.verdict, route.rule) == (VisionPolicyVerdict.CONFIRM, "V12_confirmation_required")
    rotate = _decide(policy, task_id="docs-stable", field_name="page_orientation", value="upright")
    assert (rotate.verdict, rotate.rule) == (VisionPolicyVerdict.ALLOW, "V13_allowed")
    narrow = VisionTaskPolicy(catalog={"docs-stable": stable_docs}, permitted_action_types=frozenset())
    denied = _decide(narrow, task_id="docs-stable", field_name="page_orientation", value="upright")
    assert (denied.verdict, denied.rule) == (VisionPolicyVerdict.DENY, "V4_action_not_permitted")


def test_unknown_task_status_denies():
    task = VisionTask(task_id="odd", status="preview", fields=SHAPES.fields, calibrated_models=frozenset({MODEL}))
    decision = _decide(VisionTaskPolicy(catalog={"odd": task}), task_id="odd")
    assert (decision.verdict, decision.rule) == (VisionPolicyVerdict.DENY, "V5_unknown_task_status")


def test_evaluate_reads_the_gate_proposal():
    proposal = VisionProposal(
        schema_id="shapes-v1", field="count", value=2, probability=0.97, margin=0.9, entropy=0.1,
        provenance={"model": MODEL},
    )
    assert VisionTaskPolicy().evaluate(proposal) is VisionPolicyVerdict.ALLOW


def test_catalog_schemas_are_valid_and_finite():
    for task in DEFAULT_VISION_TASK_CATALOG.values():
        schema = task.schema
        assert schema.schema_id == task.task_id
        assert {f.type for f in schema.fields} <= {"enum", "boolean", "integer", "number"}
    assert DOCS.human_review_fields == ("contains_personal_data",)


# --- escalation executor ----------------------------------------------------------------------------------


def _escalation(field="shape", target=EscalationTarget.CHAT_COMPLETION):
    return VisionEscalation(field=field, target=target, reasons=("low_probability",), probability=0.41, margin=0.05,
                            entropy=1.2, candidate="circle")


class ChatTransport:
    def __init__(self, status=200, content=None, *, raw=None, exc=None, model=MODEL):
        self.status, self.exc = status, exc
        payload = {"object": "chat.completion", "model": model,
                   "choices": [{"message": {"role": "assistant", "content": json.dumps(content or {"shape": "square"})}}]}
        self.body = raw if raw is not None else json.dumps(payload).encode()
        self.calls: list[dict] = []

    def request(self, method, url, *, body, headers, timeout_s):
        self.calls.append({"url": url, "body": json.loads(body), "headers": dict(headers), "timeout_s": timeout_s})
        if self.exc is not None:
            raise self.exc
        return self.status, self.body


def _executor(transport=None, *, larger=False, store=None):
    target = ChatTargetConfig(url="http://127.0.0.1:8096", model=MODEL, api_key=KEY)
    handler = ChatCompletionEscalationHandler(target, timeout_ms=3000, max_image_bytes=1 << 20, max_image_side=256,
                                              transport=transport)
    handlers = {EscalationTarget.LARGER_MODEL if larger else EscalationTarget.CHAT_COMPLETION: handler} if transport else {}
    return VisionEscalationExecutor(handlers, tickets=store or VoiceCommandConfirmationStore())


def test_chat_escalation_answers_only_the_escalated_fields():
    transport = ChatTransport(content={"shape": "square", "count": 3})
    results = _executor(transport).escalate(
        SHAPES, [_escalation("shape"), _escalation("count")], principal=ALICE, images=[_png()], text="hint"
    )
    assert {name: (r.status, r.handled_by, r.suggestion) for name, r in results.items()} == {
        "shape": ("answered", EscalationTarget.CHAT_COMPLETION, "square"),
        "count": ("answered", EscalationTarget.CHAT_COMPLETION, 3),
    }
    call = transport.calls[0]
    assert call["url"].endswith("/v1/chat/completions")
    schema = call["body"]["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"shape", "count"} and schema["additionalProperties"] is False
    assert schema["properties"]["shape"] == {"type": "string", "enum": ["circle", "square", "triangle"]}
    image = call["body"]["messages"][0]["content"][0]["image_url"]["url"]
    assert image.startswith("data:image/png;base64,")
    assert call["headers"]["Authorization"] == f"Bearer {KEY}" and call["body"]["model"] == MODEL
    audit = json.dumps([r.as_audit_dict() for r in results.values()])
    assert "square" not in audit and "circle" not in audit
    assert "candidate" not in json.dumps(results["shape"].as_response())


@pytest.mark.parametrize(
    ("transport", "error_code"),
    [
        (ChatTransport(status=500), "http_500"),
        (ChatTransport(status=429), "http_429"),
        (ChatTransport(exc=socket.timeout()), "deadline_exceeded"),
        (ChatTransport(exc=ConnectionRefusedError()), "unavailable"),
        (ChatTransport(raw=b"not json"), "bad_response"),
        (ChatTransport(content={"shape": "hexagon"}), "bad_response"),
        (ChatTransport(content={"shape": "square", "extra": 1}), "bad_response"),
        (ChatTransport(raw=json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()), "bad_response"),
    ],
)
def test_failed_machine_escalation_falls_back_to_a_human_ticket(transport, error_code):
    results = _executor(transport).escalate(SHAPES, [_escalation()], principal=ALICE, images=[_png()])
    result = results["shape"]
    assert (result.status, result.handled_by, result.error_code) == ("awaiting_human", EscalationTarget.HUMAN, error_code)
    assert result.suggestion is None and result.escalation_id
    assert result.reasons == ("low_probability", "chat_completion_failed")


def test_unconfigured_target_goes_to_a_human_and_human_fields_never_reach_a_model():
    transport = ChatTransport()
    executor = _executor(transport, larger=True)
    results = executor.escalate(
        SHAPES,
        [_escalation("shape"), _escalation("color", EscalationTarget.HUMAN)],
        principal=ALICE,
        images=[_png()],
    )
    assert transport.calls == []  # chat_completion is not configured here; larger_model has no escalation
    assert results["shape"].reasons[-1] == "chat_completion_unavailable"
    assert results["color"].status == "awaiting_human" and results["color"].reasons == ("low_probability",)


def test_escalation_field_bound_overflows_to_humans():
    fields = {
        f"f{i}": VisionTaskField(spec=VisionField(name=f"f{i}", type="boolean", description=f"question {i}"))
        for i in range(MAX_CHAT_FIELDS + 2)
    }
    task = VisionTask(task_id="many-v1", status="beta", fields=fields)
    content = {f"f{i}": True for i in range(MAX_CHAT_FIELDS)}
    transport = ChatTransport(content=content)
    results = _executor(transport).escalate(task, [_escalation(name) for name in fields], principal=ALICE, images=[_png()])
    assert len(transport.calls) == 1
    assert sum(r.status == "answered" for r in results.values()) == MAX_CHAT_FIELDS
    assert [r.reasons[-1] for r in results.values() if r.status == "awaiting_human"] == ["escalation_overflow"] * 2


def test_human_ticket_is_single_use_and_bound():
    store = VoiceCommandConfirmationStore()
    executor = _executor(store=store)
    result = executor.escalate(SHAPES, [_escalation()], principal=ALICE, images=[_png()])["shape"]
    action = escalation_action_type("shapes-v1", "shape")
    with pytest.raises(Exception) as foreign:
        store.consume(result.escalation_id, VoicePrincipal("tenant-b", "alice"), action_type=action)
    assert getattr(foreign.value, "code", None) == "confirmation.foreign_binding"
    # any use consumes the ticket, even a denied one
    with pytest.raises(Exception) as replay:
        store.consume(result.escalation_id, ALICE, action_type=action)
    assert getattr(replay.value, "code", None) == "confirmation.already_used"


def test_ticket_capacity_fails_closed():
    executor = _executor(store=VoiceCommandConfirmationStore(max_pending=1))
    first = executor.escalate(SHAPES, [_escalation("shape")], principal=ALICE, images=[_png()])["shape"]
    second = executor.escalate(SHAPES, [_escalation("color")], principal=ALICE, images=[_png()])["color"]
    assert first.status == "awaiting_human"
    assert (second.status, second.error_code, second.escalation_id) == ("failed", "confirmation.capacity_exceeded", None)


# --- escalation configuration -----------------------------------------------------------------------------


def test_escalation_config_defaults_and_key_scope():
    decision = _config(api_key=KEY)
    assert VisionEscalationConfig.from_env(decision, {}).chat is None
    same = VisionEscalationConfig.from_env(decision, {"VISION_DECISION_ESCALATION_CHAT": "true"})
    assert same.chat.url == decision.url and same.chat.api_key == KEY
    other = VisionEscalationConfig.from_env(
        decision,
        {"VISION_DECISION_ESCALATION_CHAT": "true", "VISION_DECISION_ESCALATION_CHAT_URL": "http://vlm-large:8080",
         "VISION_DECISION_ESCALATION_LARGER_URL": "https://big.example.org", "VISION_DECISION_ESCALATION_LARGER_MODEL": "big"},
    )
    assert other.chat.api_key is None and other.larger_model.api_key is None  # never sent to another host
    assert other.larger_model.model == "big"
    executor = build_escalation_executor(decision, other)
    assert executor.tickets is not None


@pytest.mark.parametrize(
    "env",
    [
        {"VISION_DECISION_ESCALATION_LARGER_URL": "http://big.example.org"},
        {"VISION_DECISION_ESCALATION_CHAT": "1", "VISION_DECISION_ESCALATION_CHAT_URL": "ftp://127.0.0.1"},
        {"VISION_DECISION_ESCALATION_TTL_SECONDS": "5"},
        {"VISION_DECISION_ESCALATION_TTL_SECONDS": "soon"},
    ],
)
def test_escalation_config_rejects_bad_values(env):
    with pytest.raises(VisionDecisionConfigurationError):
        VisionEscalationConfig.from_env(_config(), env)
