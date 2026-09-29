from __future__ import annotations

import base64
import http.server
import io
import json
import logging
import socket
import threading
import time

import pytest
from PIL import Image

from agent.services import vision_decision_provider as vdp
from agent.services.vision_decision_hub_gate import (
    EscalationTarget,
    VisionDecisionKind,
    VisionHubAction,
    VisionPolicyVerdict,
    gate_vision_decision,
)
from agent.services.vision_decision_provider import (
    VisionContext,
    VisionDecisionConfig,
    VisionDecisionConfigurationError,
    VisionDecisionOutcome,
    VisionDecisionProvider,
    VisionDecisionSchema,
    VisionImage,
    build_vision_decision_provider,
    get_vision_decision_provider,
    parse_decision_response,
)

KEY = "vision-decision-key-0123456789"
SECRET_PROMPT = "answer about the confidential blueprint"
SCHEMA = VisionDecisionSchema.from_mapping(
    "shapes-v1",
    {
        "shape": {"type": "enum", "choices": ["circle", "square", "triangle"], "description": "Which shape is drawn?"},
        "count": {"type": "integer", "minimum": 1, "maximum": 4, "description": "How many shapes are there?"},
        "dark_background": {"type": "boolean", "description": "Is the background dark?", "temperature": 1.7},
    },
)


def _png(size=(64, 64), color=(240, 240, 235), fmt="PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format=fmt)
    return out.getvalue()


def _config(**overrides) -> VisionDecisionConfig:
    values = {"enabled": True, "url": "http://127.0.0.1:8096", "timeout_ms": 3000}
    values.update(overrides)
    return VisionDecisionConfig(**values)


def _field(value, probability=0.99, margin=0.98, entropy=0.05, abstain=False, **extra):
    return {"value": value, "probability": probability, "scored_nodes": 1, "tree": True, "margin": margin,
            "entropy": entropy, "abstain": abstain, **extra}


def _result(fields=None, abstained=None):
    fields = fields or {"shape": _field("circle"), "count": _field(2), "dark_background": _field(False)}
    return {
        "decision": {name: raw["value"] for name, raw in fields.items()},
        "fields": fields,
        "abstained": abstained if abstained is not None else [n for n, raw in fields.items() if raw.get("abstain")],
        "usage": {"context_tokens": 88, "scored_rows": 12},
    }


def _payload(results=None, model="Qwen3-VL-2B-Instruct-Q8_0.gguf"):
    return {
        "object": "decision",
        "results": results if results is not None else [_result()],
        "model": model,
        "created": 1,
        "usage": {"prompt_tokens": 200, "cached_tokens": 116, "context_tokens": 88, "scored_rows": 12,
                  "media_chunks": 1, "media_tokens": 64, "media_cached": 1},
        "timings": {"prefill_ms": 2200.0, "media_encode_ms": 800.0, "scoring_ms": 300.0, "total_ms": 2500.0,
                    "rounds": 1, "per_decision_ms": 2500.0},
    }


class FakeTransport:
    def __init__(self, status=200, payload=None, *, raw: bytes | None = None, exc: Exception | None = None):
        self.status = status
        self.body = raw if raw is not None else json.dumps(payload if payload is not None else _payload()).encode()
        self.exc = exc
        self.calls: list[dict] = []

    def request(self, method, url, *, body, headers, timeout_s):
        self.calls.append({"method": method, "url": url, "body": json.loads(body), "headers": dict(headers),
                           "timeout_s": timeout_s})
        if self.exc is not None:
            raise self.exc
        return self.status, self.body


def _provider(transport, **overrides) -> VisionDecisionProvider:
    return VisionDecisionProvider(_config(**overrides), transport=transport)


def _decide(provider, *, images=1, text=SECRET_PROMPT, **kwargs) -> VisionDecisionOutcome:
    context = VisionContext(images=tuple(VisionImage(_png()) for _ in range(images)), text=text)
    return provider.decide(SCHEMA, [context], **kwargs)


# --- feature flag and configuration -------------------------------------------------------------------


class RecordingEnv(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.read: list[str] = []

    def get(self, key, default=None):
        self.read.append(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.read.append(key)
        return super().__getitem__(key)


def test_disabled_reads_nothing_else_and_contacts_nothing(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("no network contact allowed when disabled")

    monkeypatch.setattr(socket, "create_connection", no_network)
    env = RecordingEnv(
        VISION_DECISION_URL="http://evil.example.com",
        VISION_DECISION_API_KEY_FILE="/etc/shadow",
        VISION_DECISION_TIMEOUT_MS="not-a-number",
    )
    assert VisionDecisionConfig.from_env(env).enabled is False
    assert env.read == ["VISION_DECISION_ENABLED"]
    env.read.clear()
    assert get_vision_decision_provider(env) is None
    assert env.read == ["VISION_DECISION_ENABLED"]
    assert build_vision_decision_provider(VisionDecisionConfig()) is None
    with pytest.raises(VisionDecisionConfigurationError):
        VisionDecisionProvider(VisionDecisionConfig())


def test_enabled_config_from_env(tmp_path):
    secret = tmp_path / "key"
    secret.write_text(KEY + "\n")
    config = VisionDecisionConfig.from_env(
        {
            "VISION_DECISION_ENABLED": "true",
            "VISION_DECISION_URL": "http://172.17.0.1:8096",
            "VISION_DECISION_API_KEY_FILE": str(secret),
            "VISION_DECISION_TIMEOUT_MS": "20000",
            "VISION_DECISION_MAX_MEDIA": "2",
            "VISION_DECISION_MODELS": "Qwen3-VL-2B-Instruct-Q8_0.gguf",
            "VISION_DECISION_SCHEMAS": "shapes-v1, doc-type-v1",
        },
        secret_roots=(tmp_path,),
    )
    assert config.api_key == KEY and KEY not in repr(config)
    assert config.max_media == 2 and config.timeout_ms == 20000
    assert config.models == ("Qwen3-VL-2B-Instruct-Q8_0.gguf",)
    assert config.schemas == ("shapes-v1", "doc-type-v1")
    assert (config.min_probability, config.min_margin) == (0.8, 0.3)


@pytest.mark.parametrize(
    "overrides",
    [
        {"VISION_DECISION_URL": "ftp://127.0.0.1"},
        {"VISION_DECISION_URL": "http://decisions.example.com:8096"},
        {"VISION_DECISION_URL": "http://user:pw@127.0.0.1:8096"},
        {"VISION_DECISION_URL": "http://127.0.0.1:8096/v1/decision"},
        {"VISION_DECISION_TIMEOUT_MS": "50"},
        {"VISION_DECISION_TIMEOUT_MS": "abc"},
        {"VISION_DECISION_MAX_MEDIA": "0"},
        {"VISION_DECISION_MAX_MEDIA": "17"},
        {"VISION_DECISION_MAX_IMAGE_BYTES": str(20 * 1024 * 1024)},
        {"VISION_DECISION_MAX_IMAGE_SIDE": "8"},
        {"VISION_DECISION_API_KEY": "short"},
        {"VISION_DECISION_MIN_PROBABILITY": "0"},
        {"VISION_DECISION_MIN_MARGIN": "1.5"},
        {"VISION_DECISION_MIN_MARGIN": "nan"},
        {"VISION_DECISION_SCHEMAS": "Bad Schema!"},
        {"VISION_DECISION_MODELS": "model with space"},
    ],
)
def test_invalid_config_is_rejected(overrides):
    with pytest.raises(VisionDecisionConfigurationError):
        VisionDecisionConfig.from_env({"VISION_DECISION_ENABLED": "1", **overrides})


def test_secret_file_outside_secret_store_is_rejected(tmp_path):
    secret = tmp_path / "key"
    secret.write_text(KEY)
    with pytest.raises(VisionDecisionConfigurationError):
        VisionDecisionConfig.from_env({"VISION_DECISION_ENABLED": "1", "VISION_DECISION_API_KEY_FILE": str(secret)})


def test_https_for_remote_and_http_for_compose_name():
    assert VisionDecisionConfig.from_env(
        {"VISION_DECISION_ENABLED": "1", "VISION_DECISION_URL": "https://decisions.example.com"}
    ).enabled
    assert VisionDecisionConfig.from_env(
        {"VISION_DECISION_ENABLED": "1", "VISION_DECISION_URL": "http://vision-decision:8096"}
    ).enabled


# --- schema -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        {"text": {"type": "string", "description": "What does the label say?"}},
        {"x": {"type": "enum", "choices": ["a", "a"], "description": "d"}},
        {"x": {"type": "enum", "choices": [], "description": "d"}},
        {"x": {"type": "boolean"}},
        {"x": {"type": "number", "minimum": 0, "maximum": 1, "description": "d"}},
        {"x": {"type": "integer", "minimum": 0, "maximum": 1000, "description": "d"}},
        {"x": {"type": "boolean", "description": "d", "temperature": 0}},
        {"bad name": {"type": "boolean", "description": "d"}},
        {},
    ],
)
def test_invalid_schema_is_rejected(spec):
    with pytest.raises(ValueError):
        VisionDecisionSchema.from_mapping("s1", spec)


def test_schema_not_in_allowlist_is_not_sent():
    transport = FakeTransport()
    outcome = _decide(_provider(transport, schemas=("doc-type-v1",)))
    assert not outcome.ok and outcome.error_code == "schema_not_allowed"
    assert transport.calls == []


# --- ok and request shape -----------------------------------------------------------------------------


def test_ok_outcome_and_request_shape():
    transport = FakeTransport()
    provider = _provider(transport, api_key=KEY, models=("Qwen3-VL-2B-Instruct-Q8_0.gguf",))
    outcome = _decide(provider, return_probs=True)

    assert outcome.ok and outcome.error_code is None and outcome.grants_permission is False
    result = outcome.results[0]
    assert result.accepted == {"shape": "circle", "count": 2, "dark_background": False}
    shape = result.fields["shape"]
    assert (shape.probability, shape.margin, shape.entropy, shape.abstain) == (0.99, 0.98, 0.05, False)
    assert result.fields["dark_background"].temperature == 1.7
    assert outcome.usage.media_tokens == 64 and outcome.usage.media_cached == 1
    assert outcome.timings.total_ms == 2500.0 and outcome.timings.media_encode_ms == 800.0
    assert outcome.model == "Qwen3-VL-2B-Instruct-Q8_0.gguf"
    assert outcome.latency_ms is not None

    call = transport.calls[0]
    assert call["url"] == "http://127.0.0.1:8096/v1/decision"
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    body = call["body"]
    assert body["abstain"] == {"min_probability": 0.8, "min_margin": 0.3}
    assert body["return_probs"] is True
    assert body["model"] == "Qwen3-VL-2B-Instruct-Q8_0.gguf"
    assert "temperature" not in body  # the top-level key is one number; per-field goes into the spec
    assert body["schema"]["dark_background"]["temperature"] == 1.7
    assert body["schema"]["count"] == {"type": "integer", "description": "How many shapes are there?", "minimum": 1, "maximum": 4}
    image_part, text_part = body["contexts"][0]
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    assert text_part == {"type": "text", "text": SECRET_PROMPT}


def test_gate_ok_needs_policy_to_act():
    outcome = _decide(_provider(FakeTransport()))
    hub = gate_vision_decision(outcome)
    assert hub.kind is VisionDecisionKind.PROPOSAL
    assert {d.action for d in hub.fields.values()} == {VisionHubAction.CONFIRM}
    assert hub.actionable == {}

    seen = []

    def allow(proposal):
        seen.append(proposal)
        assert proposal.grants_permission is False
        return VisionPolicyVerdict.ALLOW

    hub = gate_vision_decision(outcome, policy=allow)
    assert hub.actionable == {"shape": "circle", "count": 2, "dark_background": False}
    assert len(seen) == 3 and hub.grants_permission is False
    assert all(d.grants_permission is False for d in hub.fields.values())

    def broken(_proposal):
        raise RuntimeError("policy bug")

    hub = gate_vision_decision(outcome, policy=broken)
    assert {d.action for d in hub.fields.values()} == {VisionHubAction.DENY}
    assert all(d.value is None for d in hub.fields.values())
    hub = gate_vision_decision(outcome, policy=lambda p: "maybe")
    assert {d.action for d in hub.fields.values()} == {VisionHubAction.DENY}


# --- abstain and escalation ---------------------------------------------------------------------------


def test_abstained_field_escalates_and_carries_no_value():
    fields = {
        "shape": _field("circle"),
        "count": _field(3, probability=0.55, margin=0.2, entropy=1.1, abstain=True),
        "dark_background": _field(True),
    }
    outcome = _decide(_provider(FakeTransport(payload=_payload([_result(fields)]))))
    assert outcome.ok
    result = outcome.results[0]
    assert result.abstained == ("count",)
    count = result.fields["count"]
    assert count.value is None and count.top_value == 3 and not count.accepted
    assert set(count.abstain_reasons) == {"server_abstain", "low_probability", "low_margin"}
    assert "count" not in result.accepted

    hub = gate_vision_decision(outcome, policy=lambda p: VisionPolicyVerdict.ALLOW)
    assert hub.kind is VisionDecisionKind.ESCALATE
    decision = hub.fields["count"]
    assert decision.action is VisionHubAction.ESCALATE and decision.value is None
    assert decision.escalation.target is EscalationTarget.CHAT_COMPLETION
    assert (decision.escalation.probability, decision.escalation.margin) == (0.55, 0.2)
    assert "count" not in hub.actionable and hub.actionable["shape"] == "circle"
    assert "candidate" not in decision.escalation.as_dict()

    hub = gate_vision_decision(outcome, human_review_fields={"count"})
    assert hub.fields["count"].escalation.target is EscalationTarget.HUMAN
    hub = gate_vision_decision(outcome, escalation_target=EscalationTarget.LARGER_MODEL)
    assert hub.escalations[0].target is EscalationTarget.LARGER_MODEL


def test_listed_in_abstained_wins_over_field_flag():
    fields = {"shape": _field("circle"), "count": _field(2), "dark_background": _field(False)}
    outcome = _decide(_provider(FakeTransport(payload=_payload([_result(fields, abstained=["shape"])]))))
    assert outcome.results[0].fields["shape"].value is None
    assert outcome.results[0].abstained == ("shape",)


def test_local_thresholds_escalate_even_if_server_does_not():
    fields = {"shape": _field("circle", probability=0.7), "count": _field(2, margin=0.1), "dark_background": _field(False)}
    outcome = _decide(_provider(FakeTransport(payload=_payload([_result(fields)]))))
    result = outcome.results[0]
    assert result.fields["shape"].abstain_reasons == ("low_probability",)
    assert result.fields["count"].abstain_reasons == ("low_margin",)
    assert result.accepted == {"dark_background": False}


def test_missing_abstain_flag_escalates():
    raw = _field("circle")
    del raw["abstain"]
    fields = {"shape": raw, "count": _field(2), "dark_background": _field(False)}
    outcome = _decide(_provider(FakeTransport(payload=_payload([_result(fields)]))))
    assert outcome.results[0].fields["shape"].abstain_reasons == ("abstain_missing",)


# --- errors: no decision, never a default label -------------------------------------------------------


@pytest.mark.parametrize(
    "status,code",
    [(400, "invalid_request"), (401, "unauthorized"), (403, "forbidden"), (404, "not_found"),
     (429, "rate_limited"), (500, "http_500"), (503, "unavailable")],
)
def test_http_errors_are_no_decision(status, code):
    error = {"error": {"code": status, "message": "boom", "type": "x"}}
    outcome = _decide(_provider(FakeTransport(status=status, payload=error)))
    assert not outcome.ok and outcome.error_code == code and outcome.http_status == status
    assert outcome.results == ()
    hub = gate_vision_decision(outcome, policy=lambda p: VisionPolicyVerdict.ALLOW)
    assert hub.kind is VisionDecisionKind.NO_DECISION and hub.fields == {} and hub.actionable == {}
    assert hub.error_code == code and hub.grants_permission is False


def test_http_error_with_non_json_body():
    outcome = _decide(_provider(FakeTransport(status=502, raw=b"<html>bad gateway</html>")))
    assert not outcome.ok and outcome.error_code == "http_502"


@pytest.mark.parametrize("exc,code", [(socket.timeout("t"), "deadline_exceeded"), (TimeoutError(), "deadline_exceeded"),
                                      (ConnectionRefusedError(), "unavailable"), (OSError("x"), "unavailable")])
def test_transport_errors_are_no_decision(exc, code):
    outcome = _decide(_provider(FakeTransport(exc=exc)))
    assert not outcome.ok and outcome.error_code == code


def test_unexpected_transport_bug_is_no_decision():
    outcome = _decide(_provider(FakeTransport(exc=RuntimeError("bug"))))
    assert not outcome.ok and outcome.error_code == "provider_error"


def _broken_payloads():
    ok = _payload()
    yield "not json", b"{not json"
    yield "list", json.dumps([1, 2]).encode()
    yield "wrong object", json.dumps({**ok, "object": "chat.completion"}).encode()
    yield "count mismatch", json.dumps(_payload([_result(), _result()])).encode()
    yield "no results", json.dumps({**ok, "results": None}).encode()
    missing = _result()
    del missing["fields"]["count"]
    yield "missing field", json.dumps(_payload([missing])).encode()
    extra = _result()
    extra["fields"]["extra"] = _field(True)
    yield "extra field", json.dumps(_payload([extra])).encode()
    for bad in ("hexagon", 7, "2", None, True):
        field = "count" if not isinstance(bad, str) or bad == "2" else "shape"
        wrong = _result()
        wrong["fields"][field]["value"] = bad
        wrong["decision"][field] = bad
        yield f"invalid value {bad!r}", json.dumps(_payload([wrong])).encode()
    prob = _result()
    prob["fields"]["shape"]["probability"] = 1.7
    yield "probability > 1", json.dumps(_payload([prob])).encode()
    noprob = _result()
    noprob["fields"]["shape"]["probability"] = "high"
    yield "probability not a number", json.dumps(_payload([noprob])).encode()
    mismatch = _result()
    mismatch["decision"]["shape"] = "square"
    yield "decision mismatch", json.dumps(_payload([mismatch])).encode()
    unknown = _result()
    unknown["abstained"] = ["nope"]
    yield "unknown abstained", json.dumps(_payload([unknown])).encode()
    noabst = _result()
    del noabst["abstained"]
    yield "abstained missing", json.dumps(_payload([noabst])).encode()


@pytest.mark.parametrize("label,raw", list(_broken_payloads()), ids=[label for label, _ in _broken_payloads()])
def test_broken_responses_are_no_decision(label, raw):
    outcome = _decide(_provider(FakeTransport(raw=raw)))
    assert not outcome.ok and outcome.error_code == "bad_response", label
    assert gate_vision_decision(outcome).kind is VisionDecisionKind.NO_DECISION


def test_model_allowlist_is_enforced_on_the_response():
    transport = FakeTransport(payload=_payload(model="/models/SmolVLM-500M-Instruct-Q8_0.gguf"))
    outcome = _decide(_provider(transport, models=("Qwen3-VL-2B-Instruct-Q8_0.gguf",)))
    assert not outcome.ok and outcome.error_code == "model_not_allowed"
    transport = FakeTransport(payload=_payload(model="/models/Qwen3-VL-2B-Instruct-Q8_0.gguf"))
    assert _decide(_provider(transport, models=("Qwen3-VL-2B-Instruct-Q8_0.gguf",))).ok


def test_parse_is_usable_standalone():
    outcome = parse_decision_response(200, json.dumps(_payload()).encode(), schema=SCHEMA, n_contexts=1,
                                      min_probability=0.8, min_margin=0.3)
    assert outcome.ok and outcome.schema_id == "shapes-v1"


# --- local image limits -------------------------------------------------------------------------------


def test_too_many_images_rejected_before_upload():
    transport = FakeTransport()
    outcome = _decide(_provider(transport, max_media=2), images=3)
    assert outcome.error_code == "too_many_images" and transport.calls == []


@pytest.mark.parametrize(
    "content,code",
    [
        (b"", "invalid_image"),
        (b"GIF89a not really", "invalid_image"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "invalid_image"),
        (_png(fmt="GIF"), "invalid_image"),
        (_png(fmt="BMP"), "invalid_image"),
    ],
)
def test_invalid_images_rejected_before_upload(content, code):
    transport = FakeTransport()
    outcome = _provider(transport).decide(SCHEMA, [VisionContext(images=(VisionImage(content),))])
    assert outcome.error_code == code and transport.calls == []


def test_image_byte_and_pixel_limits(monkeypatch):
    transport = FakeTransport()
    provider = _provider(transport, max_image_bytes=2048)
    big = io.BytesIO()
    Image.effect_noise((256, 256), 64).convert("RGB").save(big, format="PNG")
    outcome = provider.decide(SCHEMA, [VisionContext(images=(VisionImage(big.getvalue()),))])
    assert outcome.error_code == "image_too_large" and transport.calls == []

    monkeypatch.setattr(vdp, "MAX_IMAGE_PIXELS", 100)
    outcome = _provider(transport).decide(SCHEMA, [VisionContext(images=(VisionImage(_png((20, 20))),))])
    assert outcome.error_code == "image_too_large" and transport.calls == []


def test_image_is_downscaled_and_reencoded_without_metadata():
    transport = FakeTransport()
    jpeg = io.BytesIO()
    exif = Image.Exif()
    exif[0x010E] = "secret description"
    Image.new("RGB", (800, 400), (10, 20, 30)).save(jpeg, format="JPEG", exif=exif)
    outcome = _provider(transport, max_image_side=128).decide(SCHEMA, [VisionContext(images=(VisionImage(jpeg.getvalue()),))])
    assert outcome.ok
    url = transport.calls[0]["body"]["contexts"][0][0]["image_url"]["url"]
    sent = base64.b64decode(url.split(",", 1)[1])
    with Image.open(io.BytesIO(sent)) as image:
        assert image.format == "PNG" and image.size == (128, 64)
        assert not image.getexif()
    assert b"secret description" not in sent


def test_context_without_image_or_with_long_text_is_rejected():
    transport = FakeTransport()
    provider = _provider(transport)
    assert provider.decide(SCHEMA, [VisionContext(images=())]).error_code == "invalid_request"
    long_text = VisionContext(images=(VisionImage(_png()),), text="x" * 5000)
    assert provider.decide(SCHEMA, [long_text]).error_code == "invalid_request"
    assert provider.decide(SCHEMA, []).error_code == "invalid_request"
    assert transport.calls == []


def test_expired_deadline_is_not_sent():
    transport = FakeTransport()
    outcome = _decide(_provider(transport), deadline_monotonic=time.monotonic() - 1)
    assert outcome.error_code == "deadline_exceeded" and transport.calls == []


# --- logging ------------------------------------------------------------------------------------------


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


@pytest.fixture
def module_log():
    handler = _ListHandler()
    logger = logging.getLogger(vdp.__name__)
    previous = (logger.level, logger.disabled)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.disabled = False  # the app's dictConfig may disable existing loggers
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous[0])
        logger.disabled = previous[1]


def test_logs_carry_no_image_prompt_value_or_key(module_log):
    fields = {"shape": _field("triangle"), "count": _field(4, abstain=True, probability=0.4), "dark_background": _field(True)}
    transport = FakeTransport(payload=_payload([_result(fields)]))
    _decide(_provider(transport, api_key=KEY))
    _decide(_provider(FakeTransport(status=400, payload={"error": {"message": SECRET_PROMPT}}), api_key=KEY))
    text = "\n".join(module_log.lines)
    assert "vision decision schema=shapes-v1" in text
    image_b64 = transport.calls[0]["body"]["contexts"][0][0]["image_url"]["url"].split(",", 1)[1]
    for secret in (KEY, SECRET_PROMPT, "triangle", image_b64[:40], "base64"):
        assert secret not in text
    audit = json.dumps(gate_vision_decision(_decide(_provider(transport))).as_audit_dict())
    assert "triangle" not in audit and SECRET_PROMPT not in audit


# --- real sockets: timeout and refused connection -----------------------------------------------------


class _SlowHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        time.sleep(1.0)
        body = json.dumps(_payload()).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_real_http_timeout_is_no_decision():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = VisionDecisionProvider(_config(url=f"http://127.0.0.1:{server.server_port}", timeout_ms=200))
        outcome = _decide(provider)
        assert not outcome.ok and outcome.error_code == "deadline_exceeded"
        assert gate_vision_decision(outcome).kind is VisionDecisionKind.NO_DECISION
    finally:
        server.shutdown()
        server.server_close()


def test_real_connection_refused_is_unavailable():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    outcome = _decide(VisionDecisionProvider(_config(url=f"http://127.0.0.1:{port}")))
    assert not outcome.ok and outcome.error_code == "unavailable"


def test_provider_cache_follows_environment():
    env = {"VISION_DECISION_ENABLED": "true", "VISION_DECISION_URL": "http://127.0.0.1:8096"}
    first = get_vision_decision_provider(env)
    assert first is get_vision_decision_provider(dict(env))
    assert first is not get_vision_decision_provider({**env, "VISION_DECISION_MAX_MEDIA": "2"})
