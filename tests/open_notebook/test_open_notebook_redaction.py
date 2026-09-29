from agent.sources.open_notebook_redaction import (
    REDACTED_PLACEHOLDER,
    contains_secret_value,
    looks_like_secret_key,
    redact_metadata,
    redact_text,
)


def test_detects_typical_secret_keys():
    for key in ("api_key", "API-KEY", "token", "password", "secret", "authorization", "bearer_token"):
        assert looks_like_secret_key(key), key
    assert not looks_like_secret_key("title")
    assert not looks_like_secret_key("notebook_ids")


def test_detects_sk_like_values():
    assert contains_secret_value("prefix sk-abcdef1234567890 suffix")
    assert contains_secret_value("ghp-abcdefabcdef123456")
    assert not contains_secret_value("plain research text about tasks")


def test_redact_text_masks_value_and_counts():
    redacted, count = redact_text("key sk-abcdef1234567890 end")
    assert REDACTED_PLACEHOLDER in redacted
    assert "sk-abcdef1234567890" not in redacted
    assert count == 1


def test_redact_metadata_handles_nested_structures():
    payload = {
        "title": "ok",
        "api_key": "super-secret-value",
        "nested": {
            "authorization": "Bearer abc",
            "list": ["sk-abcdef1234567890", {"password": "x", "note": "fine"}],
        },
    }
    redacted, count = redact_metadata(payload)
    assert redacted["title"] == "ok"
    assert redacted["api_key"] == REDACTED_PLACEHOLDER
    assert redacted["nested"]["authorization"] == REDACTED_PLACEHOLDER
    assert redacted["nested"]["list"][0] == REDACTED_PLACEHOLDER
    assert redacted["nested"]["list"][1]["password"] == REDACTED_PLACEHOLDER
    assert redacted["nested"]["list"][1]["note"] == "fine"
    assert count == 4


def test_redact_metadata_does_not_change_clean_payload():
    payload = {"topics": ["a", "b"], "info": {"lang": "de"}}
    redacted, count = redact_metadata(payload)
    assert redacted == payload
    assert count == 0
