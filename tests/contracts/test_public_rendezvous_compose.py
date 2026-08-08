import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker/old_way/docker-compose.public-rendezvous.yml"
KEYCLOAK_CONTAINERFILE = ROOT / "public-rendezvous/keycloak/Containerfile"
KEYCLOAK_BASE = (
    "quay.io/keycloak/keycloak:26.6.1@"
    "sha256:dea26401d06341095cc4ea9d66896200b55de5ca1daa1d2fcbe58493afa6e0ad"
)
KEYCLOAK_IMAGE = "ananta-keycloak:26.6.1-optimized-v1"
KEYCLOAK_REALM = ROOT / "public-rendezvous/keycloak/ananta-realm.json"
KEYCLOAK_SETUP = ROOT / "public-rendezvous/keycloak/setup.sh"
KEYCLOAK_LOGIN_THEME = (
    ROOT / "public-rendezvous/keycloak-themes/ananta-minimal/login/theme.properties"
)
NGINX_CONFIG = ROOT / "docker/nginx/conf.d/default.conf"


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def test_public_keycloak_waits_for_postgres_and_has_native_health_probe():
    services = _services()
    keycloak = services["keycloak"]

    assert keycloak["image"] == KEYCLOAK_IMAGE
    assert keycloak["pull_policy"] == "never"
    assert "build" not in keycloak
    assert keycloak["command"][:2] == ["start", "--optimized"]
    assert keycloak["mem_limit"] == "${KEYCLOAK_MEMORY_LIMIT:-700m}"
    assert keycloak["environment"]["KC_HEALTH_ENABLED"] == "true"
    assert keycloak["environment"]["KC_METRICS_ENABLED"] == "false"
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
    assert healthcheck["interval"] == "30s"
    assert healthcheck["timeout"] == "30s"
    assert healthcheck["retries"] == 5
    assert healthcheck["start_period"] == "1800s"


def test_public_keycloak_image_is_prebuilt_without_runtime_secrets():
    containerfile = KEYCLOAK_CONTAINERFILE.read_text(encoding="utf-8")

    assert f"ARG KEYCLOAK_BASE={KEYCLOAK_BASE}" in containerfile
    assert containerfile.count("FROM ${KEYCLOAK_BASE}") == 2
    assert containerfile.count("RUN /opt/keycloak/bin/kc.sh build") == 1
    assert "KC_DB=postgres" in containerfile
    assert "KC_HEALTH_ENABLED=true" in containerfile
    assert "KC_METRICS_ENABLED=false" in containerfile
    assert "COPY --from=builder /opt/keycloak/ /opt/keycloak/" in containerfile
    assert 'ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]' in containerfile

    for runtime_secret in (
        "KC_DB_PASSWORD",
        "KC_DB_USERNAME",
        "KC_BOOTSTRAP_ADMIN",
        "KEYCLOAK_ADMIN",
        "KEYCLOAK_DB_PASSWORD",
    ):
        assert runtime_secret not in containerfile


def test_public_edge_waits_for_healthy_backends_and_bind_mounts_are_selinux_safe():
    services = _services()

    assert services["caddy"]["image"].startswith("caddy:2.11.4-alpine@sha256:")
    assert services["caddy"]["depends_on"] == {
        "keycloak": {"condition": "service_healthy"},
        "rendezvous": {"condition": "service_healthy"},
    }
    assert services["caddy"]["volumes"][0].endswith(":ro,Z")
    assert services["keycloak"]["volumes"][0].endswith(":ro,Z")
    assert services["keycloak"]["volumes"][1] == (
        "../../public-rendezvous/keycloak-themes/ananta-minimal:"
        "/opt/keycloak/themes/ananta-minimal:ro,Z"
    )


def test_public_keycloak_login_theme_limits_locale_warmup():
    realm = json.loads(KEYCLOAK_REALM.read_text(encoding="utf-8"))
    theme_properties = KEYCLOAK_LOGIN_THEME.read_text(encoding="utf-8").splitlines()

    assert realm["loginTheme"] == "ananta-minimal"
    assert "parent=keycloak" in theme_properties
    assert "locales=de,en" in theme_properties


def test_public_tui_client_explicitly_allows_pair_dev_origins():
    realm = json.loads(KEYCLOAK_REALM.read_text(encoding="utf-8"))
    setup = KEYCLOAK_SETUP.read_text(encoding="utf-8")
    tui_client = next(
        client for client in realm["clients"] if client["clientId"] == "ananta-tui"
    )

    assert tui_client["webOrigins"] == [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        "https://localhost",
        "https://127.0.0.1",
    ]
    assert "+" not in tui_client["webOrigins"]
    assert "*" not in tui_client["webOrigins"]
    assert 'webOrigins=["+"]' not in setup
    expected_setup_origins = (
        'webOrigins=["http://localhost:4200","http://127.0.0.1:4200",'
        '"https://localhost","https://127.0.0.1"]'
    )
    assert setup.count(expected_setup_origins) == 2

    for redirect_uri in (
        "https://localhost/oidc-callback",
        "https://127.0.0.1/oidc-callback",
    ):
        assert redirect_uri in tui_client["redirectUris"]
        assert setup.count(redirect_uri) == 2


def test_local_https_edge_pins_the_supported_public_oidc_issuer():
    nginx = NGINX_CONFIG.read_text(encoding="utf-8")
    csp = next(line for line in nginx.splitlines() if "Content-Security-Policy" in line)

    assert "connect-src 'self' https://keycloak.ananta.de ws: wss:;" in csp
    assert "connect-src 'self' https: " not in csp


def test_public_coturn_accepts_rendezvous_rest_credentials_only():
    coturn = _services()["coturn"]
    command = coturn["command"]

    assert coturn["image"].startswith("coturn/coturn:4.17.0@sha256:")
    assert coturn["entrypoint"] == ["turnserver"]
    assert command[0] == "-n"
    assert "--use-auth-secret" in command
    assert any(item.startswith("--static-auth-secret=${TURN_SHARED_SECRET:") for item in command)
    assert "--lt-cred-mech" not in command
    assert not any(item.startswith("--user=") for item in command)
    assert "--no-tls" in command
    assert "--no-cli" not in command
    assert "--no-dtls" not in command
    assert "--dtls" not in command
    assert not any(item.startswith("--tls-listening-port=") for item in command)
