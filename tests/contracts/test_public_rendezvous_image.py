"""Focused clean-image contracts for the standalone public rendezvous app."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE_DIR = ROOT / "public-rendezvous/rendezvous"
DOCKERFILE = SERVICE_DIR / "Dockerfile"
OPS_DOC = ROOT / "docs/ops/public-ananta-test-rendezvous.md"


def _dockerfile_copy_sources() -> tuple[str, ...]:
    sources: list[str] = []
    for raw_line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("COPY "):
            continue
        parts = shlex.split(line)
        assert len(parts) >= 3, f"unsupported Dockerfile COPY instruction: {line}"
        assert parts[-1] in {".", "./"}, f"unsupported Dockerfile COPY destination: {line}"
        sources.extend(parts[1:-1])
    return tuple(sources)


def _isolated_runtime_tree(target: Path) -> None:
    for relative_source in _dockerfile_copy_sources():
        source = SERVICE_DIR / relative_source
        assert source.is_file(), f"Dockerfile source is not a regular file: {relative_source}"
        shutil.copy2(source, target / source.name)


def _runtime_env(
    tmp_path: Path,
    *,
    signing_secret: str | None,
    turn_secret: str = "",
    cors_allowed_origins: str | None = None,
    expected_signing_key_id: str | None = None,
) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("RENDEZVOUS_SECURITY_SIGNING_SECRET", None)
    env.pop("RENDEZVOUS_EXPECTED_SIGNING_KEY_ID", None)
    env.pop("TURN_SHARED_SECRET", None)
    env.pop("CORS_ALLOWED_ORIGINS", None)
    if signing_secret is not None:
        env["RENDEZVOUS_SECURITY_SIGNING_SECRET"] = signing_secret
    if expected_signing_key_id is not None:
        env["RENDEZVOUS_EXPECTED_SIGNING_KEY_ID"] = expected_signing_key_id
    if turn_secret:
        env["TURN_SHARED_SECRET"] = turn_secret
    if cors_allowed_origins is not None:
        env["CORS_ALLOWED_ORIGINS"] = cors_allowed_origins
    env["RENDEZVOUS_DB_PATH"] = str(tmp_path / "rendezvous.db")
    return env


def _run_isolated(
    tmp_path: Path,
    script: str,
    *,
    signing_secret: str | None,
    turn_secret: str = "",
    cors_allowed_origins: str | None = None,
    expected_signing_key_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    _isolated_runtime_tree(tmp_path)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=_runtime_env(
            tmp_path,
            signing_secret=signing_secret,
            turn_secret=turn_secret,
            cors_allowed_origins=cors_allowed_origins,
            expected_signing_key_id=expected_signing_key_id,
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_clean_image_module_set_imports_app_and_serves_health(tmp_path: Path):
    result = _run_isolated(
        tmp_path,
        """
from app import app
client = app.test_client()
response = client.get('/health', headers={'Origin': 'https://localhost'})
assert response.status_code == 200
assert response.get_json() == {'ok': True, 'service': 'ananta-rendezvous'}
assert response.headers['Access-Control-Allow-Origin'] == 'https://localhost'
preflight = client.options('/rendezvous/sessions', headers={
    'Origin': 'http://localhost:4200',
    'Access-Control-Request-Method': 'GET',
})
assert preflight.status_code in {200, 204}
assert preflight.headers['Access-Control-Allow-Origin'] == 'http://localhost:4200'
denied = client.options('/rendezvous/sessions', headers={
    'Origin': 'https://untrusted.invalid',
    'Access-Control-Request-Method': 'GET',
})
assert 'Access-Control-Allow-Origin' not in denied.headers
security = client.get('/rendezvous/sessions/probe/security/key-packages')
assert security.status_code == 401
""",
        signing_secret="signing-secret-that-is-definitely-at-least-32-bytes",
        turn_secret="turn-secret-that-is-distinct-and-at-least-32-bytes",
    )

    assert result.returncode == 0, result.stderr


def test_clean_image_accepts_matching_deployment_signing_key_id(tmp_path: Path):
    result = _run_isolated(
        tmp_path,
        "import app",
        signing_secret="signing-secret-that-is-definitely-at-least-32-bytes",
        expected_signing_key_id="rv:7075fc293ced7cbe45f82eda",
    )

    assert result.returncode == 0, result.stderr


def test_clean_image_rejects_mismatched_deployment_signing_key_id_before_startup(tmp_path: Path):
    result = _run_isolated(
        tmp_path,
        "import app",
        signing_secret="signing-secret-that-is-definitely-at-least-32-bytes",
        expected_signing_key_id="rv:000000000000000000000000",
    )

    assert result.returncode != 0
    assert "rendezvous_security_signing_key_id_mismatch" in result.stderr


@pytest.mark.parametrize(
    ("signing_secret", "turn_secret", "expected_error"),
    [
        (None, "", "must be configured"),
        ("too-short", "", "must contain at least 32 bytes"),
        (
            "same-secret-material-with-at-least-32-bytes",
            "same-secret-material-with-at-least-32-bytes",
            "must be independent",
        ),
    ],
)
def test_clean_image_rejects_invalid_signing_secret(
    tmp_path: Path,
    signing_secret: str | None,
    turn_secret: str,
    expected_error: str,
):
    result = _run_isolated(
        tmp_path,
        "import config",
        signing_secret=signing_secret,
        turn_secret=turn_secret,
    )

    assert result.returncode != 0
    assert expected_error in result.stderr


@pytest.mark.parametrize(
    "cors_allowed_origins",
    [
        "*",
        "null",
        "ftp://localhost",
        "https://user@localhost",
        "https://localhost/path",
        "https://localhost?query=yes",
        "https://localhost#fragment",
    ],
)
def test_clean_image_rejects_non_origin_cors_entries(
    tmp_path: Path,
    cors_allowed_origins: str,
):
    result = _run_isolated(
        tmp_path,
        "import config",
        signing_secret="signing-secret-that-is-definitely-at-least-32-bytes",
        cors_allowed_origins=cors_allowed_origins,
    )

    assert result.returncode != 0
    assert "CORS_ALLOWED_ORIGINS" in result.stderr


def test_clean_image_normalizes_cors_scheme_host_and_default_ports(tmp_path: Path):
    result = _run_isolated(
        tmp_path,
        """
import config
assert config.CORS_ALLOWED_ORIGINS == frozenset({
    'https://localhost',
    'http://127.0.0.1',
    'https://[::1]',
})
""",
        signing_secret="signing-secret-that-is-definitely-at-least-32-bytes",
        cors_allowed_origins=("HTTPS://LOCALHOST:443,http://127.0.0.1:80,https://[0:0:0:0:0:0:0:1]:443"),
    )

    assert result.returncode == 0, result.stderr


def test_release_runbook_is_fail_closed_and_keeps_a_complete_rollback_record():
    documentation = OPS_DOC.read_text(encoding="utf-8")
    build = documentation.split("## Build and release sync", 1)[1].split("## Deploy or update Rendezvous", 1)[0]
    deploy = documentation.split("## Deploy or update Rendezvous", 1)[1].split("### Roll back Rendezvous", 1)[0]
    rollback = documentation.split("### Roll back Rendezvous", 1)[1]

    assert "set -Eeuo pipefail" in build
    assert "git fetch --prune origin main" in build
    assert 'git symbolic-ref --quiet --short HEAD)" = main' in build
    assert "git rev-parse origin/main" in build
    assert "/home/krusty/.local/state/ananta-public-rendezvous/signing-secret" in build
    assert "rv:796c1b35f1815ef88b439c40" in build
    assert "/etc/ananta/.public-rendezvous-signing-secret.pending" in build
    assert 'cat >"$incoming"' in build
    assert 'shred --remove=unlink --zero "$incoming"' in build
    assert deploy.count("set -Eeuo pipefail") >= 2
    assert 'signing=$(cat "$seed_file")' in deploy
    assert "signing=$(openssl rand" not in deploy
    assert "dst=/run/ananta-signing-seed,readonly" in deploy
    assert "actual_signing_key_id=$(sudo docker run" in deploy
    assert "discard_failed_prevalidation" in deploy
    assert "restore_pre_cutover_state" in deploy
    assert "rollback_pre_cutover_on_exit" in deploy
    assert "rollback_failed_cutover_on_exit" in deploy
    assert "cutover_started=1" in deploy
    assert "failed public cutover was rolled back automatically" in deploy
    assert 'sudo tar -C /opt/ananta -xf "$backup_dir/source.tar"' in deploy
    assert 'sudo docker tag "$previous_image_id" ananta-public-rendezvous:deployed' in deploy
    assert 'shred --remove=unlink --zero "$tmp"' in deploy
    assert 'shred --remove=unlink --zero "$seed_file"' in deploy
    assert "RENDEZVOUS_EXPECTED_SIGNING_KEY_ID" in deploy
    assert "service._SECURITY_AUTHORITY.key_id" in deploy
    assert "RENDEZVOUS_DB_PATH=/tmp/rendezvous-smoke.db" in deploy
    assert "rollback_image_ref=" in deploy
    assert ".rendezvous-previous-backup" in deploy
    assert "org.opencontainers.image.revision" in deploy
    assert "Access-Control-Allow-Origin".lower() in deploy.lower()
    assert "https://untrusted.invalid" in deploy
    assert "security/key-packages" in deploy and "= 401" in deploy
    assert "--force-recreate rendezvous coturn" in deploy
    assert "coturn hardening arguments are not active" in deploy
    assert "*:3478" in deploy
    assert "set -Eeuo pipefail" in rollback
    assert "public-rendezvous.env" in rollback
    assert "rollback-image-ref" in rollback
    assert "--force-recreate rendezvous coturn" in rollback
