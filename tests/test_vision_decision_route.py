"""``POST /v1/vision/decision`` (+ confirm, escalation resolve): images -> provider -> gate -> task policy."""
from __future__ import annotations

import base64
import http.client
import json
import logging
import socket
from unittest.mock import patch

import pytest

from agent.services import vision_decision_hub_service as hub_service
from agent.services import vision_decision_provider as vdp
from agent.services.audio_decision_command_executor import VoiceCommandConfirmationStore, VoiceCommandExecutor
from agent.services.vision_decision_escalation_executor import ChatTargetConfig, VisionEscalationConfig, build_escalation_executor
from agent.services.vision_decision_hub_service import VisionHubRuntime, default_vision_action_handlers
from agent.services.vision_decision_provider import VisionDecisionProvider
from tests.test_vision_decision_provider import KEY, _config, _field, _png

ROUTE = "/v1/vision/decision"
CONFIRM = "/v1/vision/decision/confirm"
RESOLVE = "/v1/vision/decision/escalations/resolve"
MODEL = "Qwen3-VL-2B-Instruct-Q8_0.gguf"
SECRET_TEXT = "the confidential blueprint of vault 7"
SHAPES = {"shape": "circle", "color": "red", "count": 2, "dark_background": False}
DOCS = {"page_orientation": "rotated_90", "document_kind": "invoice", "contains_personal_data": True}


@pytest.fixture(autouse=True)
def _clean_vision_env(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith("VISION_DECISION_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(vdp, "_provider_cache", None)
    monkeypatch.setattr(hub_service, "_runtime_cache", None)


def _uri(content: bytes | None = None, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(content if content is not None else _png()).decode()


def _decision(values: dict, overrides: dict | None = None, model: str = MODEL) -> dict:
    fields = {name: _field(value) for name, value in values.items()}
    fields.update(overrides or {})
    return {
        "object": "decision",
        "model": model,
        "results": [
            {
                "decision": {name: raw["value"] for name, raw in fields.items()},
                "fields": fields,
                "abstained": [name for name, raw in fields.items() if raw.get("abstain")],
                "usage": {"context_tokens": 88, "scored_rows": 12},
            }
        ],
        "usage": {"prompt_tokens": 200, "cached_tokens": 116, "media_chunks": 1, "media_tokens": 64, "media_cached": 0},
        "timings": {"prefill_ms": 900.0, "media_encode_ms": 400.0, "scoring_ms": 300.0, "total_ms": 1200.0, "rounds": 1},
    }


class Transport:
    """Routes ``/v1/decision`` and ``/v1/chat/completions`` to canned answers."""

    def __init__(self, status=200, payload=None, *, raw=None, exc=None, chat=None, chat_status=200):
        self.status, self.exc, self.chat, self.chat_status = status, exc, chat, chat_status
        self.body = raw if raw is not None else json.dumps(payload if payload is not None else _decision(SHAPES)).encode()
        self.calls: list[dict] = []

    def request(self, method, url, *, body, headers, timeout_s):
        self.calls.append({"url": url, "body": json.loads(body), "headers": dict(headers)})
        if url.endswith("/v1/chat/completions"):
            content = json.dumps(self.chat or {})
            return self.chat_status, json.dumps({"model": MODEL, "choices": [{"message": {"content": content}}]}).encode()
        if self.exc is not None:
            raise self.exc
        return self.status, self.body


def _runtime(transport, *, chat=False, tickets=None, **config) -> VisionHubRuntime:
    provider = VisionDecisionProvider(_config(**config), transport=transport)
    escalation = VisionEscalationConfig(chat=ChatTargetConfig(url=provider.config.url) if chat else None)
    return VisionHubRuntime(
        provider=provider,
        escalations=build_escalation_executor(provider.config, escalation, tickets=tickets, transport=transport),
        executor=VoiceCommandExecutor(default_vision_action_handlers()),
        confirmations=VoiceCommandConfirmationStore(),
    )


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("VISION_DECISION_ENABLED", "true")

    def install(runtime):
        return patch("agent.routes.vision_decision.get_vision_hub_runtime", return_value=runtime)

    return install


def _post(client, headers, path=ROUTE, **body):
    return client.post(path, headers=headers, json=body)


def _decide(client, headers, *, task="shapes-v1", images=None, text=SECRET_TEXT):
    return _post(client, headers, task=task, images=images if images is not None else [_uri()], text=text)


# --- feature flag, auth, configuration -------------------------------------------------------------------


def test_feature_off_answers_normal_path_without_config_or_contact(client, admin_auth_header, monkeypatch):
    contacted: list[object] = []

    def forbidden(*args, **kwargs):
        contacted.append(args)
        raise AssertionError("vision decision service must not be contacted when the feature is off")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", forbidden)
    monkeypatch.setenv("VISION_DECISION_API_KEY_FILE", "/run/secrets/does-not-exist")  # never read while off
    monkeypatch.setenv("VISION_DECISION_URL", "not a url")
    with patch("agent.routes.vision_decision.get_vision_hub_runtime", side_effect=AssertionError("read config")), patch(
        "agent.routes.vision_decision.log_audit"
    ) as audit:
        responses = [
            _decide(client, admin_auth_header),
            client.post(ROUTE, headers=admin_auth_header),
            _post(client, admin_auth_header, CONFIRM, confirmation_id="x", action_type="y", confirmed=True),
            _post(client, admin_auth_header, RESOLVE, escalation_id="x", task="shapes-v1", field="shape", value="circle"),
        ]
    for res in responses:
        assert res.status_code == 200
        assert res.get_json()["data"] == {
            "enabled": False,
            "hub_action": "normal_path",
            "error_code": "vision_decision_disabled",
            "grants_permission": False,
        }
    assert contacted == [] and audit.call_count == 0


def test_runtime_is_none_when_off_and_reads_only_the_flag():
    class RecordingEnv(dict):
        read: list[str] = []

        def get(self, key, default=None):
            self.read.append(key)
            return super().get(key, default)

    env = RecordingEnv({"VISION_DECISION_URL": "x", "VISION_DECISION_API_KEY_FILE": "/etc/shadow"})
    assert hub_service.get_vision_hub_runtime(env) is None
    assert set(env.read) == {"VISION_DECISION_ENABLED"}


@pytest.mark.parametrize("path", [ROUTE, CONFIRM, RESOLVE])
def test_routes_require_authentication(client, path):
    assert client.post(path, json={}).status_code == 401


def test_misconfigured_feature_is_a_503_without_key_material(client, admin_auth_header, monkeypatch):
    monkeypatch.setenv("VISION_DECISION_ENABLED", "true")
    monkeypatch.setenv("VISION_DECISION_API_KEY", "short-key")
    res = _decide(client, admin_auth_header)
    assert res.status_code == 503
    assert res.get_json()["data"]["error"]["code"] == "vision_decision.misconfigured"
    assert "short-key" not in res.get_data(as_text=True)


def test_exposure_policy_runs_before_anything(client, admin_auth_header, enabled):
    transport = Transport()

    class Governance:
        def resolve_exposure_policy(self, cfg):
            return {"vision_decision": {"enabled": False}}

    with enabled(_runtime(transport)), patch.object(hub_service, "get_platform_governance_service", return_value=Governance()):
        res = _decide(client, admin_auth_header)
    assert res.status_code == 403
    assert res.get_json()["data"]["error"]["code"] == "policy_denied"
    assert transport.calls == []


def test_real_runtime_is_built_from_env(monkeypatch):
    monkeypatch.setenv("VISION_DECISION_ENABLED", "true")
    monkeypatch.setenv("VISION_DECISION_ESCALATION_CHAT", "true")
    runtime = hub_service.get_vision_hub_runtime()
    assert runtime is not None and runtime is hub_service.get_vision_hub_runtime()
    assert runtime.provider.config.enabled and runtime.escalations.tickets is hub_service._tickets
    monkeypatch.setenv("VISION_DECISION_ESCALATION_TTL_SECONDS", "120")
    assert hub_service.get_vision_hub_runtime() is not runtime


# --- ok path ----------------------------------------------------------------------------------------------


def test_ok_releases_policy_allowed_values_and_audits_metadata_only(client, admin_auth_header, enabled, caplog):
    transport = Transport()
    caplog.set_level(logging.DEBUG)
    with enabled(_runtime(transport)), patch("agent.routes.vision_decision.log_audit") as audit:
        res = _decide(client, admin_auth_header)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert data["hub_action"] == "decided" and data["task"] == "shapes-v1" and data["grants_permission"] is False
    fields = data["fields"]
    assert {n: (f["hub_action"], f["rule"], f["value"]) for n, f in fields.items()} == {
        "shape": ("act", "V13_allowed", "circle"),
        "color": ("act", "V13_allowed", "red"),
        "count": ("act", "V13_allowed", 2),
        "dark_background": ("confirm", "V9_confirm_only", False),
    }
    assert all(f["grants_permission"] is False for f in fields.values())
    assert data["provenance"]["usage"]["media_tokens"] == 64
    # the request left the process as a data: URI with the hub-owned schema and thresholds
    sent = transport.calls[0]["body"]
    assert set(sent["schema"]) == set(SHAPES) and sent["abstain"] == {"min_probability": 0.8, "min_margin": 0.3}
    assert sent["contexts"][0][0]["image_url"]["url"].startswith("data:image/png;base64,")
    # audit + logs: field names, rules, scores, usage and timings - no value, image, prompt
    event, payload = audit.call_args.args
    assert event == "vision_decision"
    dumped = json.dumps(payload) + caplog.text
    for secret in ("circle", '"red"', SECRET_TEXT, "base64", _uri()[30:60]):
        assert secret not in dumped
    decision = payload["decision"]
    assert decision["fields"]["shape"]["rule"] == "V13_allowed"
    assert decision["outcome"]["provenance"]["usage"]["media_cached"] == 0
    assert decision["outcome"]["provenance"]["timings"]["total_ms"] == 1200.0


def test_uncalibrated_model_only_confirms(client, admin_auth_header, enabled):
    transport = Transport(payload=_decision(SHAPES, model="SmolVLM-500M-Instruct-Q8_0.gguf"))
    with enabled(_runtime(transport)):
        fields = _decide(client, admin_auth_header).get_json()["data"]["fields"]
    assert {f["hub_action"] for f in fields.values()} == {"confirm"}
    assert fields["shape"]["rule"] == "V10_model_not_calibrated"


# --- escalation -------------------------------------------------------------------------------------------


def _abstained_shapes() -> dict:
    return _decision(
        SHAPES,
        {
            "shape": _field("circle", probability=0.41, margin=0.05, entropy=1.2, abstain=True),
            "count": _field(2, probability=0.62, margin=0.5, abstain=False),  # below the local threshold
        },
    )


def test_uncertain_fields_escalate_to_a_human_when_no_model_target_is_configured(client, admin_auth_header, enabled):
    transport = Transport(payload=_abstained_shapes())
    with enabled(_runtime(transport)):
        data = _decide(client, admin_auth_header).get_json()["data"]
    assert data["hub_action"] == "escalate"
    shape, count = data["fields"]["shape"], data["fields"]["count"]
    for entry in (shape, count):
        assert entry["hub_action"] == "escalate" and entry["value"] is None and entry["suggestion"] is None
        assert entry["escalation"]["status"] == "awaiting_human" and entry["escalation"]["handled_by"] == "human"
        assert entry["escalation"]["escalation_id"] and entry["escalation"]["single_use"] is True
        assert "candidate" not in entry["escalation"]
    assert shape["escalation"]["reasons"] == ["server_abstain", "low_probability", "low_margin", "chat_completion_unavailable"]
    assert (shape["escalation"]["probability"], shape["escalation"]["margin"], shape["escalation"]["entropy"]) == (0.41, 0.05, 1.2)
    assert count["escalation"]["reasons"] == ["low_probability", "chat_completion_unavailable"]
    assert data["fields"]["color"]["hub_action"] == "act"
    assert len(transport.calls) == 1  # no chat call


def test_chat_escalation_is_a_suggestion_that_needs_confirmation(client, admin_auth_header, enabled):
    transport = Transport(payload=_abstained_shapes(), chat={"shape": "square", "count": 3})
    with enabled(_runtime(transport, chat=True)), patch("agent.routes.vision_decision.log_audit") as audit:
        data = _decide(client, admin_auth_header).get_json()["data"]
    shape = data["fields"]["shape"]
    assert shape["value"] is None and shape["escalation"]["status"] == "answered"
    assert shape["suggestion"] == {
        "rule": "V7_unscored_escalation_answer",
        "grants_permission": False,
        "hub_action": "confirm",
        "value": "square",
    }
    chat_call = transport.calls[1]
    assert set(chat_call["body"]["response_format"]["json_schema"]["schema"]["properties"]) == {"shape", "count"}
    assert "square" not in json.dumps(audit.call_args.args[1])


def test_failed_chat_escalation_never_yields_a_value(client, admin_auth_header, enabled):
    transport = Transport(payload=_abstained_shapes(), chat={"shape": "hexagon", "count": 3})
    with enabled(_runtime(transport, chat=True)):
        shape = _decide(client, admin_auth_header).get_json()["data"]["fields"]["shape"]
    assert shape["value"] is None and shape["suggestion"] is None
    assert shape["escalation"]["status"] == "awaiting_human" and shape["escalation"]["error_code"] == "bad_response"


def test_human_resolution_is_single_use_bound_and_policy_checked(client, admin_auth_header, user_auth_header, enabled):
    runtime = _runtime(Transport(payload=_abstained_shapes()))
    with enabled(runtime), patch("agent.routes.vision_decision.log_audit") as audit:
        fields = _decide(client, admin_auth_header).get_json()["data"]["fields"]
        shape_id = fields["shape"]["escalation"]["escalation_id"]
        count_id = fields["count"]["escalation"]["escalation_id"]
        foreign = _post(client, user_auth_header, RESOLVE, escalation_id=shape_id, task="shapes-v1", field="shape", value="square")
        replay = _post(client, admin_auth_header, RESOLVE, escalation_id=shape_id, task="shapes-v1", field="shape", value="square")
        wrong_field = _post(client, admin_auth_header, RESOLVE, escalation_id=count_id, task="shapes-v1", field="shape", value="square")
        second = _decide(client, admin_auth_header).get_json()["data"]["fields"]
        bad_value = _post(client, admin_auth_header, RESOLVE, escalation_id=second["shape"]["escalation"]["escalation_id"],
                          task="shapes-v1", field="shape", value="hexagon")
        ok = _post(client, admin_auth_header, RESOLVE, escalation_id=second["count"]["escalation"]["escalation_id"],
                   task="shapes-v1", field="count", value=3)
    assert foreign.status_code == 403 and foreign.get_json()["data"]["error"]["code"] == "escalation.foreign_binding"
    assert replay.status_code == 403 and replay.get_json()["data"]["error"]["code"] == "escalation.already_used"
    assert wrong_field.status_code == 403 and wrong_field.get_json()["data"]["error"]["code"] == "escalation.action_mismatch"
    assert bad_value.status_code == 422 and bad_value.get_json()["data"]["rule"] == "V3_value_not_allowed"
    assert ok.status_code == 200
    resolved = ok.get_json()["data"]
    assert (resolved["hub_action"], resolved["rule"], resolved["value"], resolved["grants_permission"]) == (
        "act", "V6_human_answer", 3, False,
    )
    resolutions = [c.args[1] for c in audit.call_args_list if c.args[0] == "vision_decision_escalation_resolved"]
    assert len(resolutions) == 5 and "hexagon" not in json.dumps(resolutions)


def test_expired_escalation_ticket_is_gone(client, admin_auth_header, enabled):
    now = [1000.0]
    tickets = VoiceCommandConfirmationStore(clock=lambda: now[0])
    with enabled(_runtime(Transport(payload=_abstained_shapes()), tickets=tickets)):
        fields = _decide(client, admin_auth_header).get_json()["data"]["fields"]
        now[0] += 601.0
        res = _post(client, admin_auth_header, RESOLVE, escalation_id=fields["shape"]["escalation"]["escalation_id"],
                    task="shapes-v1", field="shape", value="square")
    assert res.status_code == 410 and res.get_json()["data"]["error"]["code"] == "escalation.expired"


def test_human_review_field_skips_the_model_target(client, admin_auth_header, enabled):
    payload = _decision(DOCS, {"contains_personal_data": _field(True, probability=0.55, margin=0.1, abstain=True)})
    transport = Transport(payload=payload, chat={"contains_personal_data": False})
    with enabled(_runtime(transport, chat=True)):
        entry = _decide(client, admin_auth_header, task="document-intake-v1").get_json()["data"]["fields"]["contains_personal_data"]
    assert entry["escalation"]["target"] == "human" and entry["escalation"]["status"] == "awaiting_human"
    assert len(transport.calls) == 1


# --- actions and confirmations ----------------------------------------------------------------------------


def test_document_actions_need_an_explicit_single_use_confirmation(client, admin_auth_header, user_auth_header, enabled):
    runtime = _runtime(Transport(payload=_decision(DOCS)))
    with enabled(runtime), patch("agent.routes.vision_decision.log_audit") as audit:
        fields = _decide(client, admin_auth_header, task="document-intake-v1").get_json()["data"]["fields"]
        rotate = fields["page_orientation"]
        assert (rotate["hub_action"], rotate["rule"], rotate["execution"] if "execution" in rotate else None) == (
            "confirm", "V9_confirm_only", None,
        )
        confirmation = rotate["confirmation"]
        assert confirmation["action_type"] == "vision.document.rotate.rotated_90" and confirmation["single_use"] is True
        foreign = _post(client, user_auth_header, CONFIRM, confirmation_id=confirmation["confirmation_id"],
                        action_type=confirmation["action_type"], confirmed=True)
        route_conf = fields["document_kind"]["confirmation"]
        mismatch = _post(client, admin_auth_header, CONFIRM, confirmation_id=route_conf["confirmation_id"],
                         action_type="vision.document.rotate.upright", confirmed=True)
        fields = _decide(client, admin_auth_header, task="document-intake-v1").get_json()["data"]["fields"]
        confirmation = fields["page_orientation"]["confirmation"]
        not_bool = _post(client, admin_auth_header, CONFIRM, confirmation_id=confirmation["confirmation_id"],
                         action_type=confirmation["action_type"], confirmed="yes")
        ok = _post(client, admin_auth_header, CONFIRM, confirmation_id=confirmation["confirmation_id"],
                   action_type=confirmation["action_type"], confirmed=True)
        replay = _post(client, admin_auth_header, CONFIRM, confirmation_id=confirmation["confirmation_id"],
                       action_type=confirmation["action_type"], confirmed=True)
    assert foreign.status_code == 403 and foreign.get_json()["data"]["error"]["code"] == "confirmation.foreign_binding"
    assert mismatch.status_code == 403 and mismatch.get_json()["data"]["error"]["code"] == "confirmation.action_mismatch"
    assert not_bool.status_code == 422
    assert ok.status_code == 200
    execution = ok.get_json()["data"]["execution"]
    assert execution["status"] == "executed" and execution["effect"] == {"kind": "rotate_page", "orientation": "rotated_90"}
    assert replay.status_code == 403 and replay.get_json()["data"]["error"]["code"] == "confirmation.already_used"
    dumped = json.dumps([c.args[1] for c in audit.call_args_list])
    assert "rotated_90" not in dumped and "invoice" not in dumped and confirmation["confirmation_id"] not in dumped


def test_policy_error_denies_without_value(client, admin_auth_header, enabled):
    runtime = _runtime(Transport())

    class BrokenPolicy(type(runtime.policy)):
        def decide_proposal(self, proposal, *, source="model"):
            raise RuntimeError("policy backend down")

    runtime.policy = BrokenPolicy()
    with enabled(runtime):
        data = _decide(client, admin_auth_header).get_json()["data"]
    assert {n: (f["hub_action"], f["rule"], f.get("value")) for n, f in data["fields"].items()} == {
        name: ("deny", "policy_error", None) for name in SHAPES
    }


# --- local validation -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("images", "code"),
    [
        (["https://example.org/cat.png"], "vision_decision.invalid_image"),
        (["data:image/gif;base64,R0lGODlhAQABAAAAACw="], "vision_decision.invalid_image"),
        (["data:image/png;base64,***"], "vision_decision.invalid_image"),
        ([_uri(b"")], "vision_decision.invalid_image"),
        ([42], "vision_decision.invalid_image"),
        ([], "vision_decision.images_required"),
        ("not-a-list", "vision_decision.images_required"),
        ([_uri()] * 5, "vision_decision.too_many_images"),
        ([_uri(b"x" * 5000)], "vision_decision.image_too_large"),
    ],
)
def test_invalid_images_are_rejected_before_contact(client, admin_auth_header, enabled, images, code):
    transport = Transport()
    with enabled(_runtime(transport, max_image_bytes=4096)):
        res = _decide(client, admin_auth_header, images=images)
    assert res.status_code == 422 and res.get_json()["data"]["error"]["code"] == code
    assert transport.calls == []


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"definitely not an image", "invalid_image"),
        (_png(size=(7000, 7000)), "image_too_large"),
    ],
)
def test_undecodable_images_are_rejected_by_local_validation(client, admin_auth_header, enabled, content, code):
    transport = Transport()
    with enabled(_runtime(transport, max_image_bytes=1 << 20)):
        res = _decide(client, admin_auth_header, images=[_uri(content)])
    data = res.get_json()["data"]
    assert res.status_code == 422 and data["error_code"] == code and data["fields"] == {}
    assert transport.calls == []


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"images": [_uri()]}, 422, "vision_decision.task_required"),
        ({"task": "ocr-invoice", "images": [_uri()]}, 422, "vision_decision.task_not_supported"),
        ({"task": "shapes-v1", "images": [_uri()], "text": "x" * 4001}, 422, "vision_decision.invalid_text"),
        ({"task": "shapes-v1", "images": [_uri()], "text": 7}, 422, "vision_decision.invalid_text"),
    ],
)
def test_request_validation(client, admin_auth_header, enabled, body, status, code):
    transport = Transport()
    with enabled(_runtime(transport)):
        res = client.post(ROUTE, headers=admin_auth_header, json=body)
    assert res.status_code == status and res.get_json()["data"]["error"]["code"] == code
    assert transport.calls == []


def test_non_json_body_and_bad_deadline(client, admin_auth_header, enabled):
    transport = Transport()
    with enabled(_runtime(transport)):
        not_json = client.post(ROUTE, headers=admin_auth_header, data="task=shapes-v1")
        deadline = client.post(ROUTE, headers={**admin_auth_header, "X-Ananta-Deadline-Seconds": "soon"},
                               json={"task": "shapes-v1", "images": [_uri()]})
    assert not_json.status_code == 400
    assert deadline.status_code == 422 and deadline.get_json()["data"]["error"]["code"] == "vision_decision.invalid_deadline"
    assert transport.calls == []


def test_task_outside_the_schema_allowlist_is_rejected(client, admin_auth_header, enabled):
    transport = Transport()
    with enabled(_runtime(transport, schemas=("document-intake-v1",))):
        res = _decide(client, admin_auth_header)
    assert res.status_code == 422 and res.get_json()["data"]["error"]["code"] == "vision_decision.task_not_enabled"


def test_oversized_body_is_a_413(client, admin_auth_header, enabled):
    runtime = _runtime(Transport(), max_media=1, max_image_bytes=1024)
    with enabled(runtime), patch("agent.routes.vision_decision.get_vision_hub_runtime", return_value=runtime):
        res = _decide(client, admin_auth_header, images=[_uri(b"x" * 200_000)])
    assert res.status_code == 413
    assert hub_service.max_request_bytes(runtime.provider.config) < 200_000


# --- service failures -> normal path ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("transport", "code", "status"),
    [
        (Transport(400, {"error": {"message": "bad schema: " + SECRET_TEXT}}), "invalid_request", 400),
        (Transport(401, {"error": {"message": "invalid api key"}}), "unauthorized", 401),
        (Transport(403, {}), "forbidden", 403),
        (Transport(429, {}), "rate_limited", 429),
        (Transport(503, {}), "unavailable", 503),
        (Transport(500, raw=b"<html>"), "http_500", 500),
        (Transport(exc=socket.timeout()), "deadline_exceeded", None),
        (Transport(exc=ConnectionRefusedError()), "unavailable", None),
        (Transport(raw=b"{not json"), "bad_response", 200),
        (Transport(payload={"object": "decision", "model": MODEL, "results": []}), "bad_response", 200),
        (Transport(payload=_decision({**SHAPES, "shape": "hexagon"})), "bad_response", 200),
        (Transport(payload=_decision(SHAPES, model="other.gguf")), "model_not_allowed", 200),
    ],
)
def test_service_failures_are_normal_path_without_any_value(client, admin_auth_header, enabled, transport, code, status):
    runtime = _runtime(transport, api_key=KEY, models=(MODEL,))
    with enabled(runtime), patch("agent.routes.vision_decision.log_audit") as audit:
        res = _decide(client, admin_auth_header)
    assert res.status_code == 200
    data = res.get_json()["data"]
    assert (data["hub_action"], data["error_code"], data["http_status"]) == ("normal_path", code, status)
    assert data["fields"] == {} and data["grants_permission"] is False
    assert SECRET_TEXT not in res.get_data(as_text=True)
    assert audit.call_args.args[1]["decision"]["hub_action"] == "normal_path"
    assert KEY not in json.dumps(audit.call_args.args[1])
