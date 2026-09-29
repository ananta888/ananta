from __future__ import annotations

import pytest

from agent.services.jmap_client_service import JmapClient
from agent.services.jmap_contract_service import (
    JMAP_CORE_CAPABILITY,
    JMAP_MAIL_CAPABILITY,
    JmapContractError,
    JmapCoreLimits,
    JmapMethodCall,
    JmapResultReference,
    JmapSessionDocument,
)


class _Transport:
    def __init__(self) -> None:
        self.payload = None

    def request_json(self, **values):
        self.payload = values["payload"]
        return (
            {
                "methodResponses": [
                    ["Email/query", {"ids": ["E1"], "queryState": "q1"}, "query"],
                    ["Email/get", {"list": [{"id": "E1"}]}, "get"],
                ]
            },
            object(),
        )


def _session() -> JmapSessionDocument:
    return JmapSessionDocument(
        session_url="https://mail.example.test/.well-known/jmap",
        api_url="https://mail.example.test/api",
        download_url_template="",
        upload_url_template="",
        event_source_url_template="",
        provider_account_id="A1",
        server_capabilities=frozenset({JMAP_CORE_CAPABILITY, JMAP_MAIL_CAPABILITY}),
        account_capabilities=frozenset({JMAP_MAIL_CAPABILITY}),
        limits=JmapCoreLimits(
            maximum_request_bytes=100000,
            maximum_concurrent_requests=1,
            maximum_calls_per_request=4,
            maximum_objects_per_get=10,
            maximum_objects_per_set=10,
        ),
        state="s1",
        trusted_origin="https://mail.example.test:443",
    )


def _client(transport=None) -> JmapClient:
    return JmapClient(
        session=_session(),
        transport=transport or _Transport(),
        authorization_headers={"Authorization": "Bearer redacted"},
    )


def test_query_to_get_result_reference_is_built_and_serialized_per_rfc() -> None:
    transport = _Transport()
    result = _client(transport).call_many(
        (
            JmapMethodCall(
                "Email/query",
                {"accountId": "A1", "filter": {}},
                "query",
            ),
            JmapMethodCall.build(
                name="Email/get",
                arguments={"accountId": "A1", "properties": ["id"]},
                call_id="get",
                result_references={
                    "ids": JmapResultReference(
                        result_of="query",
                        name="Email/query",
                        path="/ids",
                    )
                },
            ),
        )
    )
    assert result.ok is True
    assert transport.payload["methodCalls"][1][1]["#ids"] == {
        "resultOf": "query",
        "name": "Email/query",
        "path": "/ids",
    }


@pytest.mark.parametrize(
    ("calls", "reason_code"),
    [
        (
            (
                JmapMethodCall(
                    "Email/get",
                    {
                        "accountId": "A1",
                        "#ids": {
                            "resultOf": "later",
                            "name": "Email/query",
                            "path": "/ids",
                        },
                    },
                    "get",
                ),
                JmapMethodCall("Email/query", {"accountId": "A1"}, "later"),
            ),
            "jmap_result_reference_forward_call",
        ),
        (
            (
                JmapMethodCall(
                    "Email/get",
                    {
                        "accountId": "A1",
                        "#ids": {
                            "resultOf": "missing",
                            "name": "Email/query",
                            "path": "/ids",
                        },
                    },
                    "get",
                ),
            ),
            "jmap_result_reference_unknown_call",
        ),
        (
            (
                JmapMethodCall("Email/query", {"accountId": "A1"}, "query"),
                JmapMethodCall(
                    "Email/get",
                    {
                        "accountId": "A1",
                        "#ids": {
                            "resultOf": "query",
                            "name": "Mailbox/query",
                            "path": "/ids",
                        },
                    },
                    "get",
                ),
            ),
            "jmap_result_reference_method_mismatch",
        ),
        (
            (
                JmapMethodCall("Email/query", {"accountId": "A1"}, "query"),
                JmapMethodCall(
                    "Email/get",
                    {
                        "accountId": "A1",
                        "#ids": {
                            "resultOf": "query",
                            "name": "Email/query",
                        },
                    },
                    "get",
                ),
            ),
            "jmap_result_reference_invalid",
        ),
    ],
)
def test_invalid_unknown_and_forward_result_references_fail_closed(calls, reason_code) -> None:
    result = _client().call_many(calls)
    assert result.ok is False
    assert result.reason_code == reason_code


def test_result_reference_builder_rejects_collision_and_malformed_pointer() -> None:
    with pytest.raises(JmapContractError, match="jmap_result_reference_invalid"):
        JmapResultReference(
            result_of="query",
            name="Email/query",
            path="ids",
        )
    with pytest.raises(JmapContractError, match="jmap_result_reference_argument_invalid"):
        JmapMethodCall.build(
            name="Email/get",
            arguments={"ids": ["E1"]},
            call_id="get",
            result_references={
                "ids": JmapResultReference(
                    result_of="query",
                    name="Email/query",
                    path="/ids",
                )
            },
        )
