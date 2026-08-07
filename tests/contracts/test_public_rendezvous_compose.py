from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker/old_way/docker-compose.public-rendezvous.yml"


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_public_keycloak_waits_for_postgres_and_has_native_health_probe():
    services = _services()
    keycloak = services["keycloak"]

    assert keycloak["image"].startswith("quay.io/keycloak/keycloak:26.6.1@sha256:")
    assert keycloak["mem_limit"] == "${KEYCLOAK_MEMORY_LIMIT:-700m}"
    assert keycloak["environment"]["KC_HEALTH_ENABLED"] == "true"
    assert keycloak["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert "pg_isready" in " ".join(services["postgres"]["healthcheck"]["test"])

    healthcheck = keycloak["healthcheck"]
    assert healthcheck["test"][:3] == ["CMD", "bash", "-ec"]
    health_probe = " ".join(healthcheck["test"][3:])
    assert "/health/ready" in health_probe
    assert "/dev/tcp/127.0.0.1/9000" in health_probe
    assert "IFS= read -r status" in health_probe
    assert 'case "$$status" in HTTP/1.[01]\\ 200\\ *' in health_probe
    assert "grep" not in health_probe
    assert "curl" not in health_probe


def test_public_edge_waits_for_healthy_backends_and_bind_mounts_are_selinux_safe():
    services = _services()

    assert services["caddy"]["image"].startswith("caddy:2.11.4-alpine@sha256:")
    assert services["caddy"]["depends_on"] == {
        "keycloak": {"condition": "service_healthy"},
        "rendezvous": {"condition": "service_healthy"},
    }
    assert services["caddy"]["volumes"][0].endswith(":ro,Z")
    assert services["keycloak"]["volumes"][0].endswith(":ro,Z")


def test_public_coturn_accepts_rendezvous_rest_credentials_only():
    coturn = _services()["coturn"]
    command = coturn["command"]

    assert coturn["image"].startswith("coturn/coturn:4.17.0@sha256:")
    assert "--use-auth-secret" in command
    assert any(item.startswith("--static-auth-secret=${TURN_SHARED_SECRET:") for item in command)
    assert "--lt-cred-mech" not in command
    assert not any(item.startswith("--user=") for item in command)
    assert "--no-tls" in command
    assert "--no-dtls" in command
    assert not any(item.startswith("--tls-listening-port=") for item in command)
