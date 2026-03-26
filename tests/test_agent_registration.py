from unittest.mock import MagicMock, patch


def test_register_agent_success(client, app):
    """Testet die erfolgreiche Registrierung eines Agenten bei erreichbarer URL."""
    with patch("agent.routes.system.http_client.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        with patch("agent.routes.system.agent_repo") as mock_repo:
            payload = {
                "name": "test-agent",
                "url": "http://test-agent:5000",
                "role": "worker",
                "worker_roles": ["coder"],
                "capabilities": ["coding"],
            }
            response = client.post("/register", json=payload)

            assert response.status_code == 200
            assert response.json["status"] == "success"
            assert response.json["data"]["status"] == "registered"
            mock_repo.save.assert_called_once()


def test_register_agent_unreachable(client, app):
    """Testet die Ablehnung der Registrierung bei nicht erreichbarer URL."""
    with patch("agent.routes.system.http_client.get") as mock_get:
        # Simuliere nicht erreichbare URL
        mock_get.return_value = None

        payload = {
            "name": "failing-agent",
            "url": "http://invalid-url",
            "role": "worker",
            "worker_roles": ["coder"],
            "capabilities": ["coding"],
        }
        response = client.post("/register", json=payload)

        assert response.status_code == 400
        assert "unreachable" in response.json["message"].lower()


def test_register_agent_with_capabilities_metadata(client, app):
    with patch("agent.routes.system.http_client.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        with patch("agent.routes.system.agent_repo") as mock_repo:
            payload = {
                "name": "planner-agent",
                "url": "http://planner-agent:5000",
                "role": "worker",
                "worker_roles": ["planner"],
                "capabilities": ["planning", "analysis"],
                "execution_limits": {"max_parallel_tasks": 2},
            }
            response = client.post("/register", json=payload)

            assert response.status_code == 200
            saved_agent = mock_repo.save.call_args[0][0]
            assert saved_agent.worker_roles == ["planner"]
            assert saved_agent.capabilities == ["planning", "analysis"]
            assert saved_agent.execution_limits["max_parallel_tasks"] == 2
            assert saved_agent.registration_validated is True
            assert saved_agent.validated_at is not None


def test_register_agent_rejects_invalid_role(client, app):
    payload = {"name": "bad-agent", "url": "http://bad-agent:5000", "role": "observer"}
    response = client.post("/register", json=payload)
    assert response.status_code == 400
    assert response.json["message"] == "invalid_agent_role"


def test_register_agent_requires_worker_capabilities(client, app):
    with patch("agent.routes.system.http_client.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        payload = {"name": "plain-worker", "url": "http://plain-worker:5000", "role": "worker"}
        response = client.post("/register", json=payload)
        assert response.status_code == 400
        assert response.json["message"] == "worker_capabilities_required"
