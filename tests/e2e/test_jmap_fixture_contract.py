from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from agent.services.jmap_http_transport import JmapHttpRequest
from tests.e2e.mail_ananta_composition_harness import ContractJmapAdapter


CORE_CAPABILITY = "urn:ietf:params:jmap:core"
MAIL_CAPABILITY = "urn:ietf:params:jmap:mail"


def _send(
    adapter: ContractJmapAdapter,
    *,
    method: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    response = adapter.send(
        JmapHttpRequest(
            method=method,
            url=(
                "http://127.0.0.1:18080/.well-known/jmap"
                if method == "GET"
                else "http://127.0.0.1:18080/jmap"
            ),
            headers={"accept": "application/json"},
            body=(
                None
                if payload is None
                else json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
            ),
            connect_timeout_seconds=1.0,
            read_timeout_seconds=1.0,
            maximum_response_bytes=1024 * 1024,
        )
    )
    assert response.status_code == 200
    return dict(json.loads(response.body))


def _call(
    adapter: ContractJmapAdapter,
    method_calls: Sequence[Sequence[Any]],
) -> dict[str, Any]:
    return _send(
        adapter,
        method="POST",
        payload={
            "using": [CORE_CAPABILITY, MAIL_CAPABILITY],
            "methodCalls": list(method_calls),
        },
    )


def test_fixture_exposes_only_standard_jmap_capabilities() -> None:
    session = _send(ContractJmapAdapter(), method="GET")

    assert set(session["capabilities"]) == {CORE_CAPABILITY, MAIL_CAPABILITY}
    assert session["primaryAccounts"] == {MAIL_CAPABILITY: "A1"}
    assert session["accounts"]["A1"]["accountCapabilities"] == {
        MAIL_CAPABILITY: {}
    }


def test_fixture_is_deterministic_and_isolated_per_instance() -> None:
    mutated = ContractJmapAdapter()
    pristine = ContractJmapAdapter()

    query = _call(
        mutated,
        [["Email/query", {"accountId": "A1"}, "query"]],
    )
    assert query["methodResponses"][0][1]["ids"] == ["E1", "E2"]

    mutation = _call(
        mutated,
        [
            [
                "Email/set",
                {
                    "accountId": "A1",
                    "ifInState": "email-state-1",
                    "update": {"E1": {"keywords/$flagged": True}},
                },
                "mutation",
            ]
        ],
    )

    assert mutation["methodResponses"][0][1]["updated"] == {"E1": None}
    assert mutated.messages["E1"]["keywords"]["$flagged"] is True
    assert "$flagged" not in pristine.messages["E1"]["keywords"]
    assert all(not method.startswith("x:") for method in mutated.calls)
