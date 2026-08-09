from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REALM_EXPORT = ROOT / "public-rendezvous/keycloak/ananta-realm.json"
SETUP_SCRIPT = ROOT / "public-rendezvous/keycloak/setup.sh"
COMPOSE = ROOT / "docker/old_way/docker-compose.public-rendezvous.yml"
CONFIG = ROOT / "public-rendezvous/rendezvous/config.py"
OPS_DOC = ROOT / "docs/ops/public-ananta-test-rendezvous.md"


def _tui_client() -> dict:
    realm = json.loads(REALM_EXPORT.read_text(encoding="utf-8"))
    return next(item for item in realm["clients"] if item["clientId"] == "ananta-tui")


def _audience_mappers() -> dict[str, dict]:
    return {
        mapper["name"]: mapper
        for mapper in _tui_client()["protocolMappers"]
        if mapper["protocolMapper"] == "oidc-audience-mapper"
    }


def test_realm_import_adds_rendezvous_audience_without_replacing_hub_audience():
    mappers = _audience_mappers()

    assert set(mappers) == {"ananta-hub-audience", "ananta-rendezvous-audience"}
    assert mappers["ananta-hub-audience"]["config"] == {
        "included.custom.audience": "ananta-hub",
        "access.token.claim": "true",
        "id.token.claim": "false",
    }
    assert mappers["ananta-rendezvous-audience"]["config"] == {
        "included.custom.audience": "ananta-rendezvous",
        "access.token.claim": "true",
        "id.token.claim": "false",
    }


def test_realm_import_assigns_builtin_basic_as_default_client_scope():
    assert "basic" in _tui_client()["defaultClientScopes"]


def test_rendezvous_defaults_to_its_dedicated_audience():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    environment = compose["services"]["rendezvous"]["environment"]

    assert environment["OIDC_AUDIENCE"] == "${OIDC_AUDIENCE:-ananta-rendezvous}"
    assert 'OIDC_AUDIENCE = _env("OIDC_AUDIENCE", "ananta-rendezvous")' in CONFIG.read_text(encoding="utf-8")


def test_release_contract_applies_keycloak_mapper_before_rendezvous_cutover():
    documentation = OPS_DOC.read_text(encoding="utf-8")
    build = documentation.split("## Build and release sync", 1)[1].split("## Deploy or update Rendezvous", 1)[0]
    deploy = documentation.split("## Deploy or update Rendezvous", 1)[1].split("### Roll back Rendezvous", 1)[0]

    for path in (
        "public-rendezvous/keycloak/ananta-realm.json",
        "public-rendezvous/keycloak/setup.sh",
    ):
        assert path in build
        assert path in deploy

    setup_position = deploy.index("exec -T keycloak bash /opt/keycloak/data/import/setup.sh")
    relogin_gate_position = deploy.index("every Pair-Dev/TUI client must perform a\nfresh login")
    cutover_position = deploy.index("up -d --no-build --no-deps --force-recreate rendezvous coturn")
    assert setup_position < relogin_gate_position < cutover_position


def test_public_strict_pair_runbook_is_scoped_to_the_supported_angular_adapter():
    documentation = OPS_DOC.read_text(encoding="utf-8")

    assert "strict public Pair flow is currently supported by the Angular Pair-Dev" in documentation
    assert "TUI\ncommands must therefore not be used" in documentation
    assert "Both computers may\nuse the same Keycloak account" in documentation
    assert "Existing v1 sessions cannot be upgraded in place" in documentation


def test_setup_idempotently_updates_audiences_and_attaches_basic_scope(tmp_path: Path):
    state_path = tmp_path / "keycloak-state.json"
    log_path = tmp_path / "kcadm-calls.jsonl"
    fake_kcadm = tmp_path / "kcadm"

    fake_kcadm.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["FAKE_KCADM_STATE"])
log_path = Path(os.environ["FAKE_KCADM_LOG"])
state = json.loads(state_path.read_text()) if state_path.exists() else {
    "realm": False,
    "client": False,
    "role": False,
    "mappers": [],
    "client_scopes": [{
        "id": "33333333-3333-3333-3333-333333333333",
        "name": "basic",
    }],
    "default_client_scope_ids": [],
}
args = sys.argv[1:]


def save():
    state_path.write_text(json.dumps(state, sort_keys=True))


def setting(name):
    for index, value in enumerate(args[:-1]):
        if value == "-s" and args[index + 1].startswith(name + "="):
            return args[index + 1].split("=", 1)[1]
    return None


with log_path.open("a") as handle:
    handle.write(json.dumps(args) + "\\n")

if args[:2] == ["config", "credentials"]:
    raise SystemExit(0)
if args[:2] == ["get", "realms/ananta"]:
    raise SystemExit(0 if state["realm"] else 1)
if args[:2] == ["create", "realms"]:
    state["realm"] = True
    save()
    raise SystemExit(0)
if args[:2] == ["get", "clients"]:
    if state["client"]:
        print("11111111-1111-1111-1111-111111111111,ananta-tui")
    raise SystemExit(0)
if args[:2] == ["create", "clients"]:
    state["client"] = True
    save()
    raise SystemExit(0)
if args[:2] == ["get", "client-scopes"]:
    for item in state["client_scopes"]:
        print(f'{item["id"]},{item["name"]}')
    raise SystemExit(0)
if args[:2] == [
    "get",
    "clients/11111111-1111-1111-1111-111111111111/default-client-scopes",
]:
    for item in state["client_scopes"]:
        if item["id"] in state["default_client_scope_ids"]:
            print(f'{item["id"]},{item["name"]}')
    raise SystemExit(0)
if args and args[0] == "update" and args[1].startswith(
    "clients/11111111-1111-1111-1111-111111111111/default-client-scopes/"
):
    scope_id = args[1].rsplit("/", 1)[1]
    if not any(item["id"] == scope_id for item in state["client_scopes"]):
        raise SystemExit(65)
    if scope_id not in state["default_client_scope_ids"]:
        state["default_client_scope_ids"].append(scope_id)
        save()
    raise SystemExit(0)
if args and args[0] == "update" and args[1].startswith("clients/") and "/protocol-mappers/" not in args[1]:
    raise SystemExit(0)
if args and args[0] == "get" and args[1].endswith("/protocol-mappers/models"):
    for item in state["mappers"]:
        print(f'{item["id"]},{item["name"]}')
    raise SystemExit(0)
if args and args[0] == "create" and args[1].endswith("/protocol-mappers/models"):
    state["mappers"].append({
        "id": f"22222222-2222-2222-2222-{len(state['mappers']) + 1:012d}",
        "name": setting("name"),
        "audience": setting('config.\"included.custom.audience\"'),
    })
    save()
    raise SystemExit(0)
if args and args[0] == "update" and "/protocol-mappers/models/" in args[1]:
    mapper_id = args[1].rsplit("/", 1)[1]
    mapper = next(item for item in state["mappers"] if item["id"] == mapper_id)
    mapper["name"] = setting("name")
    mapper["audience"] = setting('config.\"included.custom.audience\"')
    save()
    raise SystemExit(0)
if args[:2] == ["get", "roles/ananta-user"]:
    if not state["role"]:
        raise SystemExit(1)
    raise SystemExit(0)
if args[:2] == ["create", "roles"]:
    state["role"] = True
    save()
    raise SystemExit(0)
if args and args[0] == "add-roles":
    raise SystemExit(0)

raise SystemExit(f"unexpected kcadm call: {args}")
""",
        encoding="utf-8",
    )
    fake_kcadm.chmod(0o700)

    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "KCADM": str(fake_kcadm),
        "KC_URL": "http://keycloak.test.invalid",
        "KC_BOOTSTRAP_ADMIN_PASSWORD": "test-only-admin-password",
        "FAKE_KCADM_STATE": str(state_path),
        "FAKE_KCADM_LOG": str(log_path),
    }
    first = subprocess.run(
        ["bash", str(SETUP_SCRIPT)],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    second = subprocess.run(
        ["bash", str(SETUP_SCRIPT)],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert {(item["name"], item["audience"]) for item in state["mappers"]} == {
        ("ananta-hub-audience", "ananta-hub"),
        ("ananta-rendezvous-audience", "ananta-rendezvous"),
    }
    assert state["default_client_scope_ids"] == ["33333333-3333-3333-3333-333333333333"]
    assert "Erstelle Audience-Mapper 'ananta-hub-audience'" in first.stdout
    assert "Erstelle Audience-Mapper 'ananta-rendezvous-audience'" in first.stdout
    assert "Setze Default-Client-Scope 'basic'" in first.stdout
    assert "Aktualisiere Audience-Mapper 'ananta-hub-audience'" in second.stdout
    assert "Aktualisiere Audience-Mapper 'ananta-rendezvous-audience'" in second.stdout
    assert "Default-Client-Scope 'basic' ist bereits" in second.stdout

    calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    mapper_creates = [call for call in calls if call[:1] == ["create"] and call[1].endswith("/protocol-mappers/models")]
    mapper_updates = [call for call in calls if call[:1] == ["update"] and "/protocol-mappers/models/" in call[1]]
    assert len(mapper_creates) == 2
    assert len(mapper_updates) == 2
    basic_scope_attaches = [call for call in calls if call[:1] == ["update"] and "/default-client-scopes/" in call[1]]
    assert basic_scope_attaches == [
        [
            "update",
            "clients/11111111-1111-1111-1111-111111111111/default-client-scopes/33333333-3333-3333-3333-333333333333",
            "-r",
            "ananta",
        ]
    ]

    csv_reads = [
        call
        for call in calls
        if call[:1] == ["get"]
        and (
            call[1] in {"clients", "client-scopes"}
            or call[1].endswith("/default-client-scopes")
            or call[1].endswith("/protocol-mappers/models")
        )
    ]
    assert csv_reads
    assert all("--format" in call and "csv" in call and "--noquotes" in call for call in csv_reads)
    client_reads = [call for call in csv_reads if call[1] == "clients"]
    assert all("clientId=ananta-tui" in call for call in client_reads)


def test_setup_fails_closed_when_builtin_basic_scope_is_missing(tmp_path: Path):
    fake_kcadm = tmp_path / "kcadm"
    fake_kcadm.write_text(
        """#!/bin/bash
set -eu

case "${1:-}:${2:-}" in
  config:credentials|get:realms/ananta|update:clients/11111111-1111-1111-1111-111111111111)
    exit 0
    ;;
  get:clients)
    printf 'id,clientId\\n11111111-1111-1111-1111-111111111111,ananta-tui\\n'
    exit 0
    ;;
  get:client-scopes)
    printf 'id,name\\n'
    exit 0
    ;;
esac

exit 64
""",
        encoding="utf-8",
    )
    fake_kcadm.chmod(0o700)

    completed = subprocess.run(
        ["/bin/bash", str(SETUP_SCRIPT)],
        check=False,
        capture_output=True,
        env={
            "PATH": str(tmp_path),
            "KCADM": str(fake_kcadm),
            "KC_URL": "http://keycloak.test.invalid",
            "KC_BOOTSTRAP_ADMIN_PASSWORD": "test-only-admin-password",
        },
        text=True,
    )

    assert completed.returncode == 1
    assert "Client-Scope 'basic' fehlt im Realm 'ananta'" in completed.stderr


def test_setup_runs_with_only_bash_and_kcadm_available(tmp_path: Path):
    fake_kcadm = tmp_path / "kcadm"
    fake_kcadm.write_text(
        """#!/bin/bash
set -eu

case "${1:-}:${2:-}" in
  config:credentials|get:realms/ananta|add-roles:*)
    exit 0
    ;;
  get:clients)
    printf 'id,clientId\\n11111111-1111-1111-1111-111111111111,ananta-tui\\n'
    exit 0
    ;;
  get:client-scopes)
    printf 'id,name\\n33333333-3333-3333-3333-333333333333,basic\\n'
    exit 0
    ;;
  update:clients/11111111-1111-1111-1111-111111111111)
    exit 0
    ;;
  get:clients/11111111-1111-1111-1111-111111111111/default-client-scopes)
    printf 'id,name\\n33333333-3333-3333-3333-333333333333,basic\\n'
    exit 0
    ;;
  get:clients/11111111-1111-1111-1111-111111111111/protocol-mappers/models)
    printf 'id,name\\n'
    printf '22222222-2222-2222-2222-000000000001,ananta-hub-audience\\n'
    printf '22222222-2222-2222-2222-000000000002,ananta-rendezvous-audience\\n'
    exit 0
    ;;
  update:clients/11111111-1111-1111-1111-111111111111/protocol-mappers/models/*)
    exit 0
    ;;
  get:roles/ananta-user)
    exit 0
    ;;
esac

printf 'unexpected kcadm call:' >&2
printf ' %q' "$@" >&2
printf '\\n' >&2
exit 64
""",
        encoding="utf-8",
    )
    fake_kcadm.chmod(0o700)

    env = {
        "PATH": str(tmp_path),
        "KCADM": str(fake_kcadm),
        "KC_URL": "http://keycloak.test.invalid",
        "KC_BOOTSTRAP_ADMIN_PASSWORD": "test-only-admin-password",
    }
    completed = subprocess.run(
        ["/bin/bash", str(SETUP_SCRIPT)],
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )

    assert "Ananta Keycloak Setup abgeschlossen" in completed.stdout
    assert "command not found" not in completed.stderr

    setup = SETUP_SCRIPT.read_text(encoding="utf-8")
    for unavailable_command in ("awk", "grep", "python", "python3", "seq"):
        assert unavailable_command not in setup
