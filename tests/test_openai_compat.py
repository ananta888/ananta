from io import BytesIO


def test_openai_models_endpoint_lists_static_and_local_models(client, admin_auth_header, app, monkeypatch):
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "default_provider": "lmstudio",
        "default_model": "qwen2.5-coder",
    }
    app.config["PROVIDER_URLS"] = {"lmstudio": "http://127.0.0.1:1234/v1"}
    monkeypatch.setattr(
        "agent.services.openai_compat_service.list_openai_compatible_models",
        lambda base_url, timeout: [{"id": "qwen2.5-coder"}],
    )

    res = client.get("/v1/models", headers=admin_auth_header)

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["object"] == "list"
    model_ids = {item["id"] for item in payload["data"]}
    assert "openai:gpt-4o" in model_ids
    assert "codex:gpt-5-codex" in model_ids
    assert "lmstudio:qwen2.5-coder" in model_ids


def test_openai_chat_completions_uses_existing_llm_stack(client, admin_auth_header, monkeypatch):
    monkeypatch.setattr(
        "agent.services.openai_compat_service.generate_text_and_usage",
        lambda **kwargs: (
            "assistant reply",
            {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            {"text": "assistant reply"},
        ),
    )

    res = client.post(
        "/v1/chat/completions",
        headers=admin_auth_header,
        json={
            "model": "lmstudio:qwen2.5-coder",
            "messages": [
                {"role": "system", "content": "be helpful"},
                {"role": "user", "content": "say hello"},
            ],
            "temperature": 0.1,
        },
    )

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "assistant reply"
    assert payload["usage"]["total_tokens"] == 7


def test_openai_responses_endpoint_returns_output_text(client, admin_auth_header, monkeypatch):
    monkeypatch.setattr(
        "agent.services.openai_compat_service.generate_text_and_usage",
        lambda **kwargs: (
            "structured answer",
            {"prompt_tokens": 2, "completion_tokens": 5, "total_tokens": 7},
            {"text": "structured answer"},
        ),
    )

    res = client.post(
        "/v1/responses",
        headers=admin_auth_header,
        json={
            "model": "openai:gpt-4o",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "summarize"}]}],
        },
    )

    assert res.status_code == 200
    payload = res.get_json()
    assert payload["object"] == "response"
    assert payload["output_text"] == "structured answer"
    assert payload["output"][0]["content"][0]["text"] == "structured answer"


def test_openai_files_endpoints_use_artifact_layer(client, admin_auth_header):
    upload_res = client.post(
        "/v1/files",
        headers=admin_auth_header,
        data={"purpose": "assistants", "file": (BytesIO(b"hello openai file"), "notes.txt")},
        content_type="multipart/form-data",
    )
    assert upload_res.status_code == 201
    uploaded = upload_res.get_json()
    file_id = uploaded["id"]
    assert uploaded["object"] == "file"
    assert uploaded["filename"] == "notes.txt"

    list_res = client.get("/v1/files", headers=admin_auth_header)
    assert list_res.status_code == 200
    listing = list_res.get_json()
    assert listing["object"] == "list"
    assert any(item["id"] == file_id for item in listing["data"])

    detail_res = client.get(f"/v1/files/{file_id}", headers=admin_auth_header)
    assert detail_res.status_code == 200
    detail = detail_res.get_json()
    assert detail["id"] == file_id
    assert detail["filename"] == "notes.txt"


def test_openai_compat_and_llm_generate_share_hub_llm_service(client, app, admin_auth_header, monkeypatch):
    with app.app_context():
        app.config["AGENT_TOKEN"] = "secret-token"
        app.config["AGENT_CONFIG"] = {
            **(app.config.get("AGENT_CONFIG") or {}),
            "llm_config": {"provider": "lmstudio", "model": "m1", "base_url": "http://127.0.0.1:1234/v1"},
        }

    calls = []

    def _fake_generate_text(**kwargs):
        calls.append(kwargs)
        return '{"answer":"shared ok","tool_calls":[]}'

    monkeypatch.setattr("agent.services.hub_llm_service.hub_llm_service.generate_text", _fake_generate_text)

    llm_res = client.post(
        "/llm/generate",
        json={"prompt": "hello"},
        headers={"Authorization": "Bearer secret-token"},
    )
    assert llm_res.status_code == 200
    assert llm_res.json["data"]["response"] == "shared ok"

    chat_res = client.post(
        "/v1/chat/completions",
        headers=admin_auth_header,
        json={"model": "lmstudio:m1", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert chat_res.status_code == 200
    assert len(calls) == 2


def test_openai_compat_policy_can_disable_exposure(client, app, admin_auth_header):
    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **(app.config.get("AGENT_CONFIG") or {}),
            "exposure_policy": {"openai_compat": {"enabled": False}},
        }

    res = client.get("/v1/models", headers=admin_auth_header)
    assert res.status_code == 403
    payload = res.get_json()
    assert payload["message"] == "forbidden"
    assert (payload.get("data") or {}).get("details") == "openai_compat_exposure_disabled"


def test_openai_compat_policy_requires_admin_for_user_jwt(client, app):
    from agent.auth import generate_token
    from agent.config import settings

    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **(app.config.get("AGENT_CONFIG") or {}),
            "exposure_policy": {
                "openai_compat": {
                    "enabled": True,
                    "allow_user_auth": True,
                    "require_admin_for_user_auth": True,
                }
            },
        }
    token = generate_token({"sub": "user-1", "role": "user", "mfa_enabled": False}, settings.secret_key, expires_in=3600)
    user_auth_header = {"Authorization": f"Bearer {token}"}

    res = client.get("/v1/models", headers=user_auth_header)
    assert res.status_code == 403
    assert (res.get_json().get("data") or {}).get("details") == "openai_compat_admin_required"


def test_openai_compat_files_can_be_disabled_by_policy(client, app, admin_auth_header):
    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **(app.config.get("AGENT_CONFIG") or {}),
            "exposure_policy": {"openai_compat": {"enabled": True, "allow_files_api": False}},
        }

    res = client.get("/v1/files", headers=admin_auth_header)
    assert res.status_code == 403
    assert (res.get_json().get("data") or {}).get("details") == "openai_compat_files_api_disabled"


def test_openai_compat_capabilities_endpoint_reports_effective_policy(client, app, admin_auth_header):
    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **(app.config.get("AGENT_CONFIG") or {}),
            "exposure_policy": {
                "openai_compat": {
                    "enabled": True,
                    "allow_files_api": False,
                    "require_admin_for_user_auth": True,
                }
            },
        }

    res = client.get("/v1/ananta/capabilities", headers=admin_auth_header)
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["object"] == "ananta.openai_compat.capabilities"
    assert (payload.get("policy") or {}).get("enabled") is True
    assert (payload.get("features") or {}).get("files") is False
    assert (payload.get("features") or {}).get("session_metadata") is True


def test_openai_chat_completions_echoes_session_metadata(client, admin_auth_header, monkeypatch):
    monkeypatch.setattr(
        "agent.services.openai_compat_service.generate_text_and_usage",
        lambda **kwargs: (
            "assistant reply",
            {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            {"text": "assistant reply"},
        ),
    )
    res = client.post(
        "/v1/chat/completions",
        headers=admin_auth_header,
        json={
            "model": "lmstudio:qwen2.5-coder",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {"conversation_id": "conv-123"},
        },
    )
    assert res.status_code == 200
    payload = res.get_json()
    conv = payload.get("conversation") or {}
    assert conv.get("conversation_id") == "conv-123"
    assert conv.get("turn_id")


def test_openai_compat_blocks_self_call_by_instance_header(client, app, admin_auth_header):
    with app.app_context():
        app.config["AGENT_NAME"] = "hub-main"
        app.config["AGENT_CONFIG"] = {
            **(app.config.get("AGENT_CONFIG") or {}),
            "exposure_policy": {"openai_compat": {"enabled": True, "instance_id": "hub-main"}},
        }

    res = client.get("/v1/models", headers={**admin_auth_header, "X-Ananta-Instance-ID": "hub-main"})
    assert res.status_code == 403
    assert (res.get_json().get("data") or {}).get("details") == "openai_compat_self_call_blocked"


def test_openai_compat_blocks_request_when_hop_limit_exceeded(client, app, admin_auth_header):
    with app.app_context():
        app.config["AGENT_CONFIG"] = {
            **(app.config.get("AGENT_CONFIG") or {}),
            "exposure_policy": {"openai_compat": {"enabled": True, "max_hops": 2}},
        }

    res = client.get("/v1/models", headers={**admin_auth_header, "X-Ananta-Hop-Count": "3"})
    assert res.status_code == 403
    assert (res.get_json().get("data") or {}).get("details") == "openai_compat_max_hops_exceeded"
