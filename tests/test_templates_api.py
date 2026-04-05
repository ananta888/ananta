def _login_admin(client) -> str:
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    return response.json["data"]["access_token"]


def test_template_create_warns_for_unknown_variables_by_default(client):
    admin_token = _login_admin(client)
    headers = {"Authorization": f"Bearer {admin_token}"}

    response = client.post(
        "/templates",
        json={
            "name": "Warn Template",
            "description": "warn-only",
            "prompt_template": "Hallo {{agent_name}} und {{unknown_variable}}",
        },
        headers=headers,
    )

    assert response.status_code == 201
    payload = response.json["data"]
    assert payload["warnings"][0]["type"] == "unknown_variables"


def test_template_create_rejects_unknown_variables_in_strict_mode(client):
    admin_token = _login_admin(client)
    headers = {"Authorization": f"Bearer {admin_token}"}

    config_response = client.post(
        "/config",
        json={"template_variable_validation": {"strict": True}},
        headers=headers,
    )
    assert config_response.status_code == 200

    response = client.post(
        "/templates",
        json={
            "name": "Strict Template",
            "description": "strict",
            "prompt_template": "Hallo {{agent_name}} und {{unknown_variable}}",
        },
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json["message"] == "unknown_template_variables"
    assert "unknown_variable" in (response.json["data"]["unknown_variables"] or [])


def test_template_update_rejects_unknown_variables_in_strict_mode(client):
    admin_token = _login_admin(client)
    headers = {"Authorization": f"Bearer {admin_token}"}

    create_response = client.post(
        "/templates",
        json={
            "name": "Updatable Template",
            "description": "strict-update",
            "prompt_template": "Hallo {{agent_name}}",
        },
        headers=headers,
    )
    assert create_response.status_code == 201
    template_id = create_response.json["data"]["id"]

    config_response = client.post(
        "/config",
        json={"template_variable_validation": {"strict": True}},
        headers=headers,
    )
    assert config_response.status_code == 200

    response = client.patch(
        f"/templates/{template_id}",
        json={"prompt_template": "Hallo {{agent_name}} und {{unknown_variable}}"},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json["message"] == "unknown_template_variables"


def test_template_create_rejects_duplicate_name(client):
    admin_token = _login_admin(client)
    headers = {"Authorization": f"Bearer {admin_token}"}

    create_response = client.post(
        "/templates",
        json={
            "name": "Duplicate Template",
            "description": "first",
            "prompt_template": "Hallo {{agent_name}}",
        },
        headers=headers,
    )
    assert create_response.status_code == 201

    duplicate_response = client.post(
        "/templates",
        json={
            "name": "  Duplicate Template  ",
            "description": "second",
            "prompt_template": "Hallo {{agent_name}}",
        },
        headers=headers,
    )
    assert duplicate_response.status_code == 409
    assert duplicate_response.json["message"] == "template_name_exists"
    assert duplicate_response.json["data"]["name"] == "Duplicate Template"


def test_template_update_rejects_duplicate_name(client):
    admin_token = _login_admin(client)
    headers = {"Authorization": f"Bearer {admin_token}"}

    first_response = client.post(
        "/templates",
        json={
            "name": "First Template",
            "description": "first",
            "prompt_template": "Hallo {{agent_name}}",
        },
        headers=headers,
    )
    second_response = client.post(
        "/templates",
        json={
            "name": "Second Template",
            "description": "second",
            "prompt_template": "Hallo {{agent_name}}",
        },
        headers=headers,
    )
    assert first_response.status_code == 201
    assert second_response.status_code == 201

    response = client.patch(
        f"/templates/{second_response.json['data']['id']}",
        json={"name": " First Template "},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json["message"] == "template_name_exists"
    assert response.json["data"]["name"] == "First Template"
