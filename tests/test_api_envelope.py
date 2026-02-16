from agent.common.api_envelope import unwrap_api_envelope


def test_unwrap_api_envelope_nested_data_wrappers():
    payload = {"status": "success", "data": {"data": {"command": "echo ok", "reason": "r"}}}
    out = unwrap_api_envelope(payload)
    assert out == {"command": "echo ok", "reason": "r"}


def test_unwrap_api_envelope_non_dict_returns_empty():
    assert unwrap_api_envelope("text") == {}
    assert unwrap_api_envelope(None) == {}
