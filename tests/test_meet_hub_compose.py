"""Read-only actual Compose rendering with synthetic scope, never operator secrets."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "docker-compose.meet-media-hub.yml"
FLAGS = (
    "ANANTA_MEET_ROOM_ALLOCATION_ENABLED",
    "ANANTA_MEET_ORGANIZATION_PRINCIPALS_ENABLED",
    "ANANTA_MEET_DIALOG_PREAUTHORIZATION_ENABLED",
    "ANANTA_MEET_DIALOG_RECONNECT",
    "ANANTA_MEET_MEDIA_TIMING",
    "ANANTA_MEET_SPEAKER_FLOOR",
)
BASE = """services:
  ai-agent-hub:
    image: ananta-synthetic-compose-only:local
    environment:
      BASE_SENTINEL: preserved
    networks: [existing-hub]
networks:
  existing-hub: {}
"""


def render(tmp_path, options=None, *, env_file=None):
    environment = {"PATH": os.environ["PATH"], "MEET_MEDIA_STATE_DIR": str(tmp_path / "synthetic-state")}
    environment.update(options or {})
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file or "/dev/null"),
            "-p",
            "meet-hub-render-only",
            "-f",
            "-",
            "-f",
            str(OVERLAY),
            "config",
            "--format",
            "json",
        ],
        input=BASE,
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, "synthetic Compose render failed; no raw configuration diagnostic"
    return json.loads(result.stdout)


def test_hub_overlay_defaults_leave_new_policy_and_features_disabled(tmp_path):
    service = render(tmp_path)["services"]["ai-agent-hub"]
    environment = service["environment"]
    for key in FLAGS:
        assert environment[key] == "0"
    assert environment["ANANTA_MEET_MACHINE_KEY_ID"] == ""
    assert environment["ANANTA_MEET_ROOM_ALLOCATION_SCOPES"] == "[]"
    assert environment["ANANTA_MEET_SPEAKER_POLICIES"] == "[]"
    assert environment["ANANTA_MEET_DIALOG_CAPACITY"] == "{}"
    assert environment["ANANTA_MEET_DIALOG_CAPACITY_POOL"] == "local-meet-dialog"
    # Null Compose forwarding must not synthesize the opt-in publisher selector.
    assert environment.get("ANANTA_MEET_DIALOG_WORKER_URLS") is None
    assert environment["BASE_SENTINEL"] == "preserved"
    assert environment["ANANTA_MEET_ENABLED"] == environment["ANANTA_MEET_DIALOG_ENABLED"] == "0"
    assert set(service["networks"]) == {"existing-hub", "meet-compute"}
    assert service["networks"]["meet-compute"]["aliases"] == ["meet-authorizing-hub"]
    mounts = {row["target"]: row for row in service["volumes"]}
    assert set(mounts) == {"/run/secrets/meet-worker-key", "/run/secrets/meet-machine-private.pem"}
    assert all(
        row["read_only"] and row["source"].startswith(str(tmp_path / "synthetic-state")) for row in mounts.values()
    )
    assert not service.get("ports") and not service.get("privileged")
    assert not (tmp_path / "synthetic-state").exists()


def test_hub_overlay_forwards_exact_explicit_operator_values_without_granting_scope(tmp_path):
    options = {key: "1" for key in FLAGS}
    options.update(
        {
            "ANANTA_MEET_MACHINE_KEY_ID": "synthetic-next-key",
            "ANANTA_MEET_ROOM_ALLOCATION_SCOPES": '[["synthetic-tenant","synthetic-project"]]',
            "ANANTA_MEET_SPEAKER_POLICIES": "[]",
            "ANANTA_MEET_DIALOG_CAPACITY": '{"sessions":2,"publisher_sessions":1,"publication_bps":32000000}',
            "ANANTA_MEET_DIALOG_CAPACITY_POOL": "synthetic-pool",
            "ANANTA_MEET_DIALOG_WORKER_URLS": '["http://synthetic-worker-2:8094/v1/turns"]',
        }
    )
    environment = render(tmp_path, options)["services"]["ai-agent-hub"]["environment"]
    assert {key: environment[key] for key in options} == options
    assert environment["ANANTA_MEET_DIALOG_POLICIES"] == "[]"
    assert environment["ANANTA_MEET_MEDIA_ALLOWED_SCOPES"] == "[]"
    assert environment["ANANTA_MEET_MEDIA_WORKER_URL"] == "http://meet-media-worker:8094/v1/turns"


@pytest.mark.parametrize("value", ["", "[]", "not-json"])
def test_optional_publisher_value_is_not_defaulted_or_repaired(tmp_path, value):
    environment = render(tmp_path, {"ANANTA_MEET_DIALOG_WORKER_URLS": value})["services"]["ai-agent-hub"]["environment"]
    assert environment["ANANTA_MEET_DIALOG_WORKER_URLS"] == value


def test_optional_publisher_can_be_supplied_by_explicit_env_file(tmp_path):
    selected = '["http://synthetic-worker-2:8094/v1/turns"]'
    env_file = tmp_path / "synthetic.env"
    env_file.write_text(f"ANANTA_MEET_DIALOG_WORKER_URLS='{selected}'\n", encoding="utf-8")
    environment = render(tmp_path, env_file=env_file)["services"]["ai-agent-hub"]["environment"]
    assert environment["ANANTA_MEET_DIALOG_WORKER_URLS"] == selected


@pytest.mark.parametrize("value", ["invalid", ""])
def test_invalid_explicit_feature_value_reaches_the_existing_hub_validator(tmp_path, value):
    environment = render(tmp_path, {"ANANTA_MEET_MEDIA_TIMING": value})["services"]["ai-agent-hub"]["environment"]
    assert environment["ANANTA_MEET_MEDIA_TIMING"] == value


def test_explicit_empty_policy_json_is_not_replaced_with_a_default(tmp_path):
    options = {
        key: ""
        for key in (
            "ANANTA_MEET_ROOM_ALLOCATION_SCOPES",
            "ANANTA_MEET_SPEAKER_POLICIES",
            "ANANTA_MEET_DIALOG_CAPACITY",
        )
    }
    environment = render(tmp_path, options)["services"]["ai-agent-hub"]["environment"]
    assert {key: environment[key] for key in options} == options
