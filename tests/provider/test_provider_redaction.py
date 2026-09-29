from __future__ import annotations

from agent.providers.redaction import redact_provider_payload


def test_redaction_masks_nested_secret_like_keys() -> None:
    payload = {
        "token": "abc",
        "nested": {
            "api_key": "secret",
            "items": [{"password": "pw"}, {"safe": "ok"}],
        },
    }
    redacted = redact_provider_payload(payload)
    assert redacted["token"] == "***REDACTED***"
    assert redacted["nested"]["api_key"] == "***REDACTED***"
    assert redacted["nested"]["items"][0]["password"] == "***REDACTED***"
    assert redacted["nested"]["items"][1]["safe"] == "ok"


def test_redaction_masks_configured_secret_refs_in_values() -> None:
    payload = {"secret_ref": "vault://my-token", "metadata": {"trace": "ok"}}
    redacted = redact_provider_payload(payload, secret_refs=["vault://my-token"])
    assert redacted["secret_ref"] == "***REDACTED***"
    assert redacted["metadata"]["trace"] == "ok"


def test_redaction_preserves_protocol_token_limits_only() -> None:
    payload = {
        "max_tokens": 321,
        "completion_tokens": 12,
        "max_completion_tokens": None,
        "token": "real-secret",
        "max_output_tokens": "string-secret",
    }

    redacted = redact_provider_payload(payload)

    assert redacted["max_tokens"] == 321
    assert redacted["completion_tokens"] == 12
    assert redacted["max_completion_tokens"] is None
    assert redacted["token"] == "***REDACTED***"
    assert redacted["max_output_tokens"] == "***REDACTED***"


def test_redaction_keeps_safe_payload_unchanged() -> None:
    payload = {"status": "ok", "count": 2, "tags": ["a", "b"]}
    assert redact_provider_payload(payload) == payload


def test_tool_parameter_schemas_named_like_secrets_stay_intact():
    tool = {"type": "function", "function": {"name": "summarize", "parameters": {
        "type": "object", "properties": {"max_tokens": {"type": "integer"}, "token": {"type": "string"}},
        "required": ["token"]}}}
    payload = {"messages": [], "tools": [tool], "api_key": "sk-live"}

    redacted = redact_provider_payload(payload, secret_refs=["sk-live"])

    assert redacted["tools"] == [tool]  # parameter names are not secrets; the schema must reach the model
    assert redacted["api_key"] == "***REDACTED***"


def test_secret_values_inside_schemas_are_still_redacted_by_reference():
    payload = {"tools": [{"function": {"parameters": {"properties": {"token": {"default": "sk-live"}}}}}]}

    redacted = redact_provider_payload(payload, secret_refs=["sk-live"])

    assert redacted["tools"][0]["function"]["parameters"]["properties"]["token"]["default"] == "***REDACTED***"


def test_token_counts_and_limits_are_not_redacted():
    payload = {"max_context_tokens": 32768, "max_output_tokens": None, "cached_tokens": 12,
               "refresh_tokens": "rt-secret", "session_token": 5}

    redacted = redact_provider_payload(payload)

    assert redacted["max_context_tokens"] == 32768 and redacted["cached_tokens"] == 12
    assert redacted["max_output_tokens"] is None
    assert redacted["refresh_tokens"] == "***REDACTED***"  # a string under a *_tokens key stays secret
    assert redacted["session_token"] == "***REDACTED***"  # not a count: singular token
