import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEV_AUTH_COMPOSE = (
    ROOT / "docker" / "compose-next" / "compose.workflow-runtime.dev-auth.yml"
)
ENTRYPOINT = ROOT / "scripts" / "quickstart-single-image-entrypoint.sh"


def _run_migration_policy(tmp_path: Path, value: str) -> tuple[int, list[str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    migration_log = tmp_path / "migration.log"
    fake_alembic = fake_bin / "alembic"
    fake_alembic.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$ANANTA_MIGRATION_TEST_LOG"\n',
        encoding="utf-8",
    )
    fake_alembic.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "ANANTA_MIGRATION_TEST_LOG": str(migration_log),
            "ANANTA_RUN_DB_MIGRATIONS": value,
            "PATH": f"{fake_bin}:{environment['PATH']}",
        }
    )
    completed = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; run_db_migrations_if_enabled',
            "migration-policy-test",
            str(ENTRYPOINT),
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    calls = (
        migration_log.read_text(encoding="utf-8").splitlines()
        if migration_log.exists()
        else []
    )
    return completed.returncode, calls


def test_quickstart_dockerfile_uses_role_entrypoint_and_exposes_fullstack_ports() -> None:
    dockerfile = (ROOT / "docker" / "compose-next" / "Dockerfile.quickstart-no-ollama").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["/app/scripts/quickstart-single-image-entrypoint.sh"]' in dockerfile
    assert "EXPOSE 5000 5001 4200 8080" in dockerfile
    assert "services/evolver_bridge" in dockerfile
    assert "opencode-ai@" in dockerfile
    assert "ollama/ollama" not in dockerfile
    assert "ARG ANANTA_RUNTIME_UID=1000" in dockerfile
    assert "ARG ANANTA_RUNTIME_GID=1000" in dockerfile
    assert "--no-create-home" in dockerfile


def test_quickstart_entrypoint_supports_single_image_roles_and_openai_guard() -> None:
    entrypoint = (ROOT / "scripts" / "quickstart-single-image-entrypoint.sh").read_text(encoding="utf-8")
    worker_body = entrypoint.split("run_worker() {", 1)[1].split(
        "\n}",
        1,
    )[0]
    agent_only_body = entrypoint.split("run_agent_only() {", 1)[1].split(
        "\n}",
        1,
    )[0]
    single_container_body = entrypoint.split(
        "run_single_container() {",
        1,
    )[1].split("\n}", 1)[0]

    assert "set -euo pipefail" in entrypoint
    assert "ANANTA_QUICKSTART_MODE" in entrypoint
    assert "ANANTA_QUICKSTART_ROLE" in entrypoint
    assert "ANANTA_FRONTEND_DISABLE_HOST_CHECK" in entrypoint
    assert "command+=(--disable-host-check)" in entrypoint
    assert "single-container" in entrypoint
    assert "agent-only" in entrypoint
    assert "evolver_bridge" in entrypoint
    assert "deerflow_runner" in entrypoint
    assert "ml_intern_runner" in entrypoint
    assert "DEFAULT_PROVIDER=openai requires OPENAI_API_KEY" in entrypoint
    assert worker_body.index("run_db_migrations_if_enabled") < worker_body.index(
        "exec python -m agent.ai_agent"
    )
    assert "prepare_runtime_directories" in agent_only_body
    assert "prepare_runtime_directories" in single_container_body


def test_dev_auth_overlay_assigns_database_migrations_only_to_hub() -> None:
    services = yaml.safe_load(DEV_AUTH_COMPOSE.read_text(encoding="utf-8"))[
        "services"
    ]

    assert services["ai-agent-hub"]["environment"][
        "ANANTA_RUN_DB_MIGRATIONS"
    ] == "1"
    for worker_name in ("ai-agent-alpha", "ai-agent-beta"):
        assert services[worker_name]["environment"][
            "ANANTA_RUN_DB_MIGRATIONS"
        ] == "0"

    for service_name in ("ai-agent-hub", "ai-agent-alpha", "ai-agent-beta"):
        assert services[service_name]["command"] == "python -m agent.ai_agent"


@pytest.mark.parametrize(
    ("value", "expected_returncode", "expected_calls"),
    [
        ("0", 0, []),
        ("1", 0, ["upgrade head"]),
        ("invalid", 64, []),
    ],
)
def test_entrypoint_database_migration_policy(
    tmp_path: Path,
    value: str,
    expected_returncode: int,
    expected_calls: list[str],
) -> None:
    returncode, calls = _run_migration_policy(tmp_path, value)

    assert returncode == expected_returncode
    assert calls == expected_calls


def test_readme_documents_single_image_fullstack_path() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docker/compose-next/compose.stack.full.yml" in readme
    assert "docker/compose-next/compose.stack.quickstart.yml" in readme
    assert "docker/old_way/" in readme
