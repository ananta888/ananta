from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_ROOT = ROOT / "docker" / "compose-next"


def _load(name: str) -> dict:
    return yaml.safe_load((COMPOSE_ROOT / name).read_text(encoding="utf-8"))


def test_compose_base_enables_only_explicit_local_secret_derivation() -> None:
    document = _load("compose.base.yml")

    for service_name in ("ai-agent-hub-base", "ai-agent-worker-base"):
        environment = document["services"][service_name]["environment"]
        assert environment[
            "ANANTA_SOURCE_ACCESS_ALLOW_COMPOSE_SECRET_DERIVATION"
        ] == "1"
        assert environment["ANANTA_RUNTIME_PROFILE"] == (
            "${ANANTA_RUNTIME_PROFILE:-compose-safe}"
        )
        assert environment["SECRET_KEY"] == (
            "${SECRET_KEY:-ananta-dev-shared-secret-change-me}"
        )
        assert environment["ANANTA_SOURCE_ACCESS_KEYRING_FILE"] == (
            "${ANANTA_SOURCE_ACCESS_KEYRING_FILE:-}"
        )


def test_dev_auth_overlay_uses_file_managed_source_access_keyring() -> None:
    base = _load("compose.base.yml")
    test_stack = _load("compose.tests.lmstudio.yml")
    overlay = _load("compose.workflow-runtime.dev-auth.yml")
    base_services = {
        "ai-agent-hub": "ai-agent-hub-base",
        "ai-agent-alpha": "ai-agent-worker-base",
        "ai-agent-beta": "ai-agent-worker-base",
    }

    for service_name, base_name in base_services.items():
        effective = {
            **base["services"][base_name]["environment"],
            **test_stack["services"][service_name]["environment"],
            **overlay["services"][service_name]["environment"],
        }
        assert effective[
            "ANANTA_SOURCE_ACCESS_ALLOW_COMPOSE_SECRET_DERIVATION"
        ] == "0"
        assert effective["ANANTA_SOURCE_ACCESS_KEYRING_FILE"].endswith(
            "/source-access-hmac-keyring.json"
        )

    hub = overlay["services"]["ai-agent-hub"]
    source_access_mount = next(
        mount
        for mount in hub["volumes"]
        if mount["target"] == "/run/ananta-source-access"
    )
    assert source_access_mount["source"].endswith("/worker")
    assert source_access_mount["read_only"] is True
    assert source_access_mount["bind"]["create_host_path"] is False


def test_every_compose_next_worker_has_an_explicit_index_identity() -> None:
    for file_name in (
        "compose.stack.quickstart.yml",
        "compose.stack.full.yml",
        "compose.stack.distributed.yml",
    ):
        document = _load(file_name)
        workers = {
            name: service
            for name, service in document["services"].items()
            if name.startswith("ai-agent-") and name != "ai-agent-hub"
        }
        assert workers
        for service in workers.values():
            environment = service["environment"]
            assert environment["ANANTA_KNOWLEDGE_INDEX_WORKER_ID"] == (
                environment["AGENT_NAME"]
            )
