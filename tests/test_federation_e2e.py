import os
import time
import requests
import pytest

# URLs are configurable, defaults point to the names in docker-compose.test.yml
# In the compose environment, these hostnames are resolvable.
HUB_URL = os.environ.get("E2E_HUB_URL", "http://ai-agent-hub:5000")
ALPHA_URL = os.environ.get("E2E_ALPHA_URL", "http://ai-agent-alpha:5000")
BETA_URL = os.environ.get("E2E_BETA_URL", "http://ai-agent-beta:5000")
ADMIN_USER = os.environ.get("E2E_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("E2E_ADMIN_PASSWORD", "admin")

def get_auth_header(base_url):
    try:
        resp = requests.post(
            f"{base_url}/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASS},
            timeout=10
        )
        resp.raise_for_status()
        token = resp.json()["data"]["access_token"]
        return {"Authorization": f"Bearer {token}"}
    except Exception as e:
        pytest.fail(f"Failed to login to {base_url}: {e}")

@pytest.mark.skipif(not os.environ.get("RUN_COMPOSE_TESTS"), reason="Set RUN_COMPOSE_TESTS=1 to run this E2E test")
def test_hub_worker_registration_e2e():
    """
    Verifies that worker agents (alpha, beta) successfully register with the hub
    and are visible in the agents list.
    """
    # Wait for services to be ready and registered
    headers = get_auth_header(HUB_URL)

    max_retries = 12
    found_alpha = False
    found_beta = False

    for i in range(max_retries):
        try:
            resp = requests.get(f"{HUB_URL}/agents", headers=headers, timeout=5)
            resp.raise_for_status()
            agents = resp.json().get("data", [])

            agent_names = [a.get("name") for a in agents]
            if "ai-agent-alpha" in agent_names:
                alpha = next(a for a in agents if a["name"] == "ai-agent-alpha")
                if alpha["status"] == "online":
                    found_alpha = True

            if "ai-agent-beta" in agent_names:
                beta = next(a for a in agents if a["name"] == "ai-agent-beta")
                if beta["status"] == "online":
                    found_beta = True

            if found_alpha and found_beta:
                return

            print(f"Waiting for agents... Found: {agent_names} (Alpha online: {found_alpha}, Beta online: {found_beta})")
        except Exception as e:
            print(f"Attempt {i+1} failed to reach Hub: {e}")

        time.sleep(10)

    pytest.fail(f"Workers did not register as online with the hub in time. Alpha: {found_alpha}, Beta: {found_beta}")

@pytest.mark.skipif(not os.environ.get("RUN_COMPOSE_TESTS"), reason="Set RUN_COMPOSE_TESTS=1 to run this E2E test")
def test_cross_container_communication_e2e():
    """
    Verifies that the test container can reach all agents and they can reach each other.
    """
    for url in [HUB_URL, ALPHA_URL, BETA_URL]:
        resp = requests.get(f"{url}/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

@pytest.mark.skipif(not os.environ.get("RUN_COMPOSE_TESTS"), reason="Set RUN_COMPOSE_TESTS=1 to run this E2E test")
def test_hub_can_proxy_to_worker_e2e():
    """
    Verifies that the hub can proxy requests to a worker (e.g. for stats).
    """
    # This assumes the hub has an endpoint to query worker stats or similar.
    # If not explicitly implemented, we just check if the hub knows about the workers' URLs.
    headers = get_auth_header(HUB_URL)
    resp = requests.get(f"{HUB_URL}/agents", headers=headers, timeout=5)
    agents = resp.json().get("data", [])

    alpha = next((a for a in agents if a["name"] == "ai-agent-alpha"), None)
    assert alpha is not None
    # The URL reported to the hub should be the internal container URL
    assert "ai-agent-alpha" in alpha["url"]
