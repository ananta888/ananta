"""Integration test against a real llama.cpp-vision-decision ``llama-server``.

Reported as SKIPPED (never as passed) unless a real server with a vision model is reachable:

* ``VISION_DECISION_IT_URL``: e.g. ``http://172.17.0.1:8096`` for a host server seen from the hub container,
  started with ``--mmproj`` and ``--decision-seqs`` (see docs/vision-decision-llamacpp.md);
* optional ``VISION_DECISION_IT_API_KEY`` (server ``--api-key``), ``VISION_DECISION_IT_MODEL`` (model allowlist
  entry, e.g. ``Qwen3-VL-2B-Instruct-Q8_0.gguf``), ``VISION_DECISION_IT_N`` (shape images, default 8),
  ``VISION_DECISION_IT_IMAGE_DIR`` (extra real images, e.g. ``/models/vlm``; decisions are recorded, not scored).

The labelled images are drawn like the fork's ``bench/shapes.py`` (coloured shapes, known truth). The accuracy
bar only applies to fields that were not abstained; abstained fields must escalate and carry no value.
"""
from __future__ import annotations

import io
import json
import os
import random
import urllib.request
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from agent.services.vision_decision_hub_gate import VisionDecisionKind, VisionHubAction, gate_vision_decision
from agent.services.vision_decision_provider import (
    HttpClientTransport,
    VisionContext,
    VisionDecisionConfig,
    VisionDecisionProvider,
    VisionDecisionSchema,
    VisionImage,
    parse_decision_response,
)

pytestmark = pytest.mark.integration

COLORS = {"red": (220, 40, 40), "green": (40, 170, 60), "blue": (40, 80, 220), "yellow": (235, 200, 30)}
SHAPES = ["circle", "square", "triangle"]
SCHEMA = VisionDecisionSchema.from_mapping(
    "shapes-v1",
    {
        "shape": {"type": "enum", "choices": SHAPES, "description": "Which shape is drawn?"},
        "color": {"type": "enum", "choices": list(COLORS), "description": "What colour are the shapes?"},
        "count": {"type": "integer", "minimum": 1, "maximum": 4, "description": "How many shapes are there?"},
        "dark_background": {"type": "boolean", "description": "Is the background dark?"},
    },
)


def _draw(rng: random.Random, size: int = 256) -> tuple[bytes, dict]:
    shape, color, count = rng.choice(SHAPES), rng.choice(list(COLORS)), rng.randint(1, 4)
    dark = rng.random() < 0.5
    img = Image.new("RGB", (size, size), (25, 25, 30) if dark else (240, 240, 235))
    d = ImageDraw.Draw(img)
    cell = size // 2
    for s in rng.sample(range(4), count):
        cx, cy = (s % 2) * cell + cell // 2, (s // 2) * cell + cell // 2
        r = rng.randint(cell // 4, cell // 3)
        box = (cx - r, cy - r, cx + r, cy + r)
        if shape == "circle":
            d.ellipse(box, fill=COLORS[color])
        elif shape == "square":
            d.rectangle(box, fill=COLORS[color])
        else:
            d.polygon([(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)], fill=COLORS[color])
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue(), {"shape": shape, "color": color, "count": count, "dark_background": dark}


@pytest.fixture(scope="module")
def provider() -> VisionDecisionProvider:
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
    return VisionDecisionProvider(config)


def _decide_or_skip(provider, contexts, **kwargs):
    outcome = provider.decide(SCHEMA, contexts, **kwargs)
    if not outcome.ok and outcome.error_code == "invalid_request" and "decision" in (outcome.error_message or ""):
        pytest.skip(f"server has no vision decision support: {outcome.error_message}")
    return outcome


def test_real_shapes_single_image(provider):
    n = int(os.environ.get("VISION_DECISION_IT_N", "8"))
    rng = random.Random(1)
    rows = []
    correct = accepted = 0
    for i in range(n):
        png, truth = _draw(rng)
        outcome = _decide_or_skip(provider, [VisionContext(images=(VisionImage(png),), text="Answer the questions about this picture.")], return_probs=True)
        assert outcome.ok, (outcome.error_code, outcome.error_message)
        assert outcome.usage.media_tokens and outcome.usage.media_tokens > 0
        assert outcome.timings.total_ms and outcome.timings.total_ms > 0
        result = outcome.results[0]
        hub = gate_vision_decision(outcome)
        assert hub.grants_permission is False
        for name, item in result.fields.items():
            assert SCHEMA.field(name).allowed(item.top_value)  # schema-valid, which is not the same as correct
            decision = hub.fields[name]
            if item.abstain:
                assert item.value is None and decision.action is VisionHubAction.ESCALATE and decision.escalation
            else:
                accepted += 1
                correct += item.value == truth[name]
                assert decision.action is VisionHubAction.CONFIRM  # no policy: never ACT
        rows.append({
            "image": i,
            "truth": truth,
            "decision": {k: v.top_value for k, v in result.fields.items()},
            "probability": {k: round(v.probability, 4) for k, v in result.fields.items()},
            "margin": {k: None if v.margin is None else round(v.margin, 4) for k, v in result.fields.items()},
            "abstained": list(result.abstained),
            "media_tokens": outcome.usage.media_tokens,
            "media_cached": outcome.usage.media_cached,
            "total_ms": round(outcome.timings.total_ms or 0, 1),
            "model": outcome.model,
        })
    print("\nVISION_DECISION_IT_RESULTS " + json.dumps({"n": n, "accepted": accepted, "correct": correct, "rows": rows}))
    assert accepted > 0, "every field abstained"
    assert correct / accepted >= 0.9, f"accuracy of accepted fields {correct}/{accepted}"


def test_real_two_contexts_and_warm_cache(provider):
    rng = random.Random(7)
    images = [_draw(rng) for _ in range(2)]
    contexts = [VisionContext(images=(VisionImage(png),)) for png, _ in images]
    cold = _decide_or_skip(provider, contexts)
    warm = _decide_or_skip(provider, contexts)
    assert cold.ok and warm.ok
    assert len(cold.results) == 2
    assert warm.usage.media_cached == 2  # encoded images are reused across requests
    for a, b in zip(cold.results, warm.results):
        assert {k: v.top_value for k, v in a.fields.items()} == {k: v.top_value for k, v in b.fields.items()}
    print("\nVISION_DECISION_IT_CACHE " + json.dumps({"cold": cold.timings.as_dict(), "warm": warm.timings.as_dict(),
                                                     "cold_usage": cold.usage.as_dict(), "warm_usage": warm.usage.as_dict()}))


def test_real_ambiguous_image_escalates(provider):
    # A blank grey image has no shape, colour or count to answer: those fields must abstain and escalate.
    out = io.BytesIO()
    Image.new("RGB", (256, 256), (128, 128, 128)).save(out, format="PNG")
    outcome = _decide_or_skip(provider, [VisionContext(images=(VisionImage(out.getvalue()),))])
    assert outcome.ok, (outcome.error_code, outcome.error_message)
    result = outcome.results[0]
    hub = gate_vision_decision(outcome)
    assert result.abstained, "no field abstained on a blank image"
    assert hub.kind is VisionDecisionKind.ESCALATE
    for name in result.abstained:
        assert result.fields[name].value is None and hub.fields[name].action is VisionHubAction.ESCALATE
        assert name not in result.accepted
    print("\nVISION_DECISION_IT_ABSTAIN " + json.dumps({"abstained": list(result.abstained),
                                                       "escalations": [e.as_dict() for e in hub.escalations]}))


def test_real_http_400_is_no_decision(provider):
    # Bypass Ananta's local validation to see the server's own 400 mapped fail-closed.
    status, body = HttpClientTransport().request(
        "POST", provider.config.url.rstrip("/") + "/v1/decision", body=json.dumps({"schema": SCHEMA.as_request(), "contexts": []}).encode(),
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {provider.config.api_key}"} if provider.config.api_key else {})},
        timeout_s=30,
    )
    outcome = parse_decision_response(status, body, schema=SCHEMA, n_contexts=1, min_probability=0.8, min_margin=0.3)
    assert status == 400 and not outcome.ok and outcome.error_code == "invalid_request"
    assert gate_vision_decision(outcome).kind is VisionDecisionKind.NO_DECISION


def test_real_extra_images_are_recorded(provider):
    directory = os.environ.get("VISION_DECISION_IT_IMAGE_DIR", "").strip()
    paths = sorted(Path(directory).glob("*.png"))[:4] if directory else []
    if not paths:
        pytest.skip("VISION_DECISION_IT_IMAGE_DIR not set or empty")
    schema = VisionDecisionSchema.from_mapping(
        "object-kind-v1",
        {
            "kind": {"type": "enum", "choices": ["animal", "vehicle", "other"], "description": "What is shown in the picture?"},
            "outdoor": {"type": "boolean", "description": "Is the picture taken outdoors?"},
        },
    )
    rows = []
    for path in paths:
        outcome = provider.decide(schema, [VisionContext(images=(VisionImage(path.read_bytes()),))])
        assert outcome.ok, (outcome.error_code, outcome.error_message)
        result = outcome.results[0]
        rows.append({"image": path.name, "decision": {k: v.top_value for k, v in result.fields.items()},
                     "probability": {k: round(v.probability, 4) for k, v in result.fields.items()},
                     "abstained": list(result.abstained), "total_ms": round(outcome.timings.total_ms or 0, 1)})
    print("\nVISION_DECISION_IT_EXTRA " + json.dumps(rows))
