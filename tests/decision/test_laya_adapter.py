"""The local laya-serve adapter: Jev wire reuse, local trust policy, key handling."""

from __future__ import annotations

import io
import json

import pytest

from agent.services.decision_providers.config import normalize_decision_config
from agent.services.decision_providers.laya import LayaDecisionProvider
from agent.services.decision_providers.types import (
    DecisionProviderError,
    DecisionQuestion,
    DecisionRequest,
)


class _Response(io.BytesIO):
    def __init__(self, payload: dict, status: int = 200):
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.status = status
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    def __init__(self, *responses: _Response):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append({"url": request.full_url, "data": request.data, "timeout": timeout})
        return self.responses.pop(0)


REQUEST = DecisionRequest(
    state="the ticket says the invoice was charged twice",
    questions=(
        DecisionQuestion.noul("double_charge", "Was the invoice charged twice?"),
        DecisionQuestion.choice(
            "department", "Which department handles it?", {"billing": "refunds", "tech": "outages"}
        ),
        DecisionQuestion.score("urgency", "How urgent?", ("low", "high")),
    ),
)

LAYA_RESPONSE = {
    "model": "english",
    "answers": {
        "double_charge": {"type": "noul", "noul": 0.91},
        "department": {
            "type": "choice",
            "choice": "billing",
            "probabilities": {"billing": 0.9, "tech": 0.1},
            "confidence": 0.8,
        },
        "urgency": {
            "type": "score",
            "score": 1.0,
            "probabilities": {"0": 0.1, "1": 0.9},
            "confidence": 0.8,
            "legend": {"0": "low", "1": "high"},
        },
    },
    "usage": {"input_tokens": 21, "output_tokens": 7},
    "routing": {"model": "english", "reason": "language"},
}


def _provider(opener, **overrides):
    return LayaDecisionProvider(
        base_url="http://laya:8080",
        allowed_hosts=["laya", "localhost", "127.0.0.1"],
        api_key=lambda: None,
        opener=opener,
        **overrides,
    )


def test_answers_follow_the_jev_wire_contract():
    opener = _Opener(_Response(LAYA_RESPONSE))
    result = _provider(opener).decide(REQUEST)

    body = json.loads(opener.requests[0]["data"])
    assert body["state"] == REQUEST.state
    assert body["questions"]["double_charge"]["type"] == "noul"
    assert body["questions"]["department"]["criteria"] == {"billing": "refunds", "tech": "outages"}

    double = result.answers["double_charge"]
    assert double.probability == pytest.approx(0.91)
    assert double.confidence == pytest.approx(0.82)  # |2p - 1|
    assert result.answers["department"].choice == "billing"
    assert result.answers["urgency"].level == 1
    assert result.provider_id == "laya"
    assert result.usage.input_tokens == 21


def test_a_keyless_server_still_gets_a_bearer_header():
    seen = {}

    def opener(request, timeout=None):
        seen["authorization"] = request.get_header("Authorization")
        return _Response(LAYA_RESPONSE)

    _provider(opener).decide(REQUEST)
    assert seen["authorization"] == "Bearer laya-local"


def test_a_configured_key_travels():
    seen = {}

    def opener(request, timeout=None):
        seen["authorization"] = request.get_header("Authorization")
        return _Response(LAYA_RESPONSE)

    provider = LayaDecisionProvider(
        base_url="http://laya:8080",
        allowed_hosts=["laya"],
        api_key=lambda: "shared-secret",
        opener=opener,
    )
    provider.decide(REQUEST)
    assert seen["authorization"] == "Bearer shared-secret"


@pytest.mark.parametrize(
    "url",
    ["http://evil.example:8080", "https://api.typesafe.ai", "file:///etc/passwd", "http://laya"],
)
def test_endpoints_outside_the_allowed_local_hosts_are_rejected(url):
    with pytest.raises(DecisionProviderError):
        LayaDecisionProvider(
            base_url=url, allowed_hosts=["laya", "localhost", "127.0.0.1"], api_key=lambda: None, opener=_Opener()
        )


def test_health_probe_answers_availability():
    seen = {}

    def opener(request, timeout=None):
        seen["url"] = request.full_url
        return _Response({"status": "ok"})

    provider = LayaDecisionProvider(
        base_url="http://laya:8080", allowed_hosts=["laya"], api_key=lambda: None, opener=opener
    )
    assert provider.is_available() == (True, "ok")
    assert seen["url"] == "http://laya:8080/health"
    unreachable = LayaDecisionProvider(
        base_url="http://laya:8080",
        allowed_hosts=["laya"],
        api_key=lambda: None,
        opener=lambda *a, **k: (_ for _ in ()).throw(OSError("down")),
    )
    assert unreachable.is_available() == (False, "server_unreachable")


def test_oversized_states_are_rejected_before_the_wire():
    provider = _provider(_Opener(), max_state_chars=1000)
    request = DecisionRequest(state="x" * 1001, questions=(DecisionQuestion.noul("q", "Yes?"),))
    with pytest.raises(DecisionProviderError) as excinfo:
        provider.decide(request)
    assert excinfo.value.reason_code == "request_too_large"


def test_config_builds_an_agreement_of_laya_and_local_decision():
    config = normalize_decision_config(
        {
            "enabled": True,
            "providers": {
                "laya": {"enabled": True, "base_url": "http://laya:8080"},
                "local_decision": {"enabled": True, "base_url": "http://egpu-code:18150"},
            },
            "areas": {
                "tool_routing": {"mode": "shadow", "cascade": [["laya", "local_decision"]], "question_thresholds": {}},
                "retrieval_intent": {
                    "mode": "shadow",
                    "cascade": ["laya", "local_decision"],
                    "question_thresholds": {},
                },
            },
        }
    )
    assert config["providers"]["laya"]["base_url"] == "http://laya:8080"
    tool_routing = config["areas"]["tool_routing"]
    assert tool_routing["cascade"] == [["laya", "local_decision"]]
    assert config["areas"]["retrieval_intent"]["cascade"] == ["laya", "local_decision"]
