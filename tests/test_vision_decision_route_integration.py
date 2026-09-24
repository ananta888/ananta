"""``POST /v1/vision/decision`` end to end against a real llama.cpp-vision-decision ``llama-server``.

Reported as SKIPPED (never as passed) unless ``RUN_INTEGRATION_TESTS=1`` and a real server with a vision model
is reachable at ``VISION_DECISION_IT_URL`` (see tests/test_vision_decision_integration.py and
docs/vision-decision-llamacpp.md). Optional: ``VISION_DECISION_IT_API_KEY``, ``VISION_DECISION_IT_MODEL``
(allowlist entry; with ``Qwen3-VL-2B-Instruct-Q8_0.gguf`` the shapes task is calibrated) and
``VISION_DECISION_IT_CHAT=1`` (also exercise the chat-completion escalation on the same server).

The route runs with the real provider, gate, task policy and escalation executor; only the Flask test client
replaces the network in front of the hub.
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import urllib.request
from unittest.mock import patch

import pytest
from PIL import Image

from agent.services.audio_decision_command_executor import VoiceCommandConfirmationStore, VoiceCommandExecutor
from agent.services.vision_decision_escalation_executor import ChatTargetConfig, VisionEscalationConfig, build_escalation_executor
from agent.services.vision_decision_hub_service import VisionHubRuntime, default_vision_action_handlers
from agent.services.vision_decision_provider import VisionDecisionConfig, VisionDecisionProvider
from agent.services.vision_decision_task_policy import DEFAULT_VISION_TASK_CATALOG
from tests.test_vision_decision_integration import _draw

pytestmark = pytest.mark.integration

ROUTE = "/v1/vision/decision"
SHAPES = DEFAULT_VISION_TASK_CATALOG["shapes-v1"]


@pytest.fixture(scope="module")
def runtime() -> VisionHubRuntime:
    url = os.environ.get("VISION_DECISION_IT_URL", "").strip()
    if not url:
        pytest.skip("VISION_DECISION_IT_URL not set: no real llama-server, test not run")
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=3) as response:
            if response.status != 200:
                pytest.skip(f"llama-server at {url} is not healthy")
    except OSError as exc:
        pytest.skip(f"llama-server at {url} not reachable ({type(exc).__name__})")
    model = os.environ.get("VISION_DECISION_IT_MODEL", "").strip()
    config = VisionDecisionConfig(
        enabled=True,
        url=url,
        api_key=os.environ.get("VISION_DECISION_IT_API_KEY", "").strip() or None,
        timeout_ms=180_000,
        max_media=4,
        models=(model,) if model else (),
    )
    chat = ChatTargetConfig(url=url, api_key=config.api_key) if os.environ.get("VISION_DECISION_IT_CHAT") == "1" else None
    provider = VisionDecisionProvider(config)
    return VisionHubRuntime(
        provider=provider,
        escalations=build_escalation_executor(config, VisionEscalationConfig(chat=chat, timeout_ms=180_000)),
        executor=VoiceCommandExecutor(default_vision_action_handlers()),
        confirmations=VoiceCommandConfirmationStore(),
    )


def _uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _post(client, headers, runtime, png: bytes) -> dict:
    with patch.dict(os.environ, {"VISION_DECISION_ENABLED": "true"}), patch(
        "agent.routes.vision_decision.get_vision_hub_runtime", return_value=runtime
    ):
        res = client.post(ROUTE, headers=headers, json={"task": "shapes-v1", "images": [_uri(png)]})
    assert res.status_code == 200, res.get_data(as_text=True)[:300]
    data = res.get_json()["data"]
    if data["hub_action"] == "normal_path" and data["error_code"] == "invalid_request":
        pytest.skip("server has no vision decision support")
    return data


def _check_invariants(data: dict) -> None:
    assert data["grants_permission"] is False
    for name, entry in data["fields"].items():
        assert entry["grants_permission"] is False
        spec = SHAPES.fields[name].spec
        if entry["hub_action"] == "escalate":
            assert entry["value"] is None and entry["escalation"]["status"] in {"answered", "awaiting_human"}
            if entry["suggestion"] is not None:
                assert entry["suggestion"]["hub_action"] in {"confirm", "deny"}
                assert entry["suggestion"]["hub_action"] == "deny" or spec.allowed(entry["suggestion"]["value"])
        elif entry["hub_action"] in {"act", "confirm"}:
            assert spec.allowed(entry["value"])  # schema-valid, which is not the same as correct


def test_route_on_real_shapes(client, admin_auth_header, runtime):
    rng = random.Random(7)
    rows = []
    for _ in range(4):
        png, truth = _draw(rng)
        data = _post(client, admin_auth_header, runtime, png)
        assert data["hub_action"] in {"decided", "escalate"}, data.get("error_code")
        _check_invariants(data)
        assert data["provenance"]["usage"]["media_tokens"] > 0
        acted = {n: e["value"] for n, e in data["fields"].items() if e["hub_action"] == "act"}
        rows.append({"truth": truth, "act": acted, "actions": {n: (e["hub_action"], e["rule"]) for n, e in data["fields"].items()},
                     "total_ms": data["provenance"]["timings"]["total_ms"]})
        # dark_background is confirm-only (V9): never act, whatever the probability
        assert data["fields"]["dark_background"]["hub_action"] != "act"
    print(json.dumps(rows, indent=1))


def test_route_escalates_an_ill_posed_question(client, admin_auth_header, runtime):
    out = io.BytesIO()
    Image.new("RGB", (256, 256), (128, 128, 128)).save(out, format="PNG")
    data = _post(client, admin_auth_header, runtime, out.getvalue())
    _check_invariants(data)
    escalated = [n for n, e in data["fields"].items() if e["hub_action"] == "escalate"]
    print(json.dumps({n: {k: data["fields"][n][k] for k in ("hub_action", "rule", "probability", "margin")} for n in data["fields"]}))
    print(json.dumps({n: data["fields"][n]["escalation"] | {"escalation_id": "<redacted>"} for n in escalated}, default=str))
    assert escalated, "a blank grey image must leave at least one field uncertain"
