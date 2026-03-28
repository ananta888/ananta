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
