"""Read-only deployment observations cannot imply admission or disclose config secrets."""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts.meet_live_observation import container_observation, public_observation, readiness_report

ORIGIN = "https://meet.example.test"


@pytest.mark.parametrize("local", [False, True])
def test_https_reader_is_bounded_headless_without_proxy_credentials_redirects_or_curlrc(local):
    execute = Mock(return_value=SimpleNamespace(stdout=b'{"status":"ok"}'))
    assert public_observation(ORIGIN, "/healthz", local_tls_route=local, execute=execute) == {"status": "ok"}
    args = execute.call_args.args[0]
    assert args[:2] == ["curl", "--disable"]
    assert args[-1] == ORIGIN + "/healthz"
    assert args[args.index("--noproxy") + 1] == "*"
    assert args[args.index("--proto") + 1] == "=https"
    assert args[args.index("--max-time") + 1] == "10"
    assert "--location" not in args and "--insecure" not in args and "--user" not in args
    assert ("--resolve" in args) is local
    if local:
        assert args[args.index("--resolve") + 1] == "meet.example.test:443:127.0.0.1"
    assert execute.call_args.kwargs["timeout"] == 12
    assert execute.call_args.kwargs["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize("body", [b"[]", b"null", b"PRIVATE-MARKER", b'{"status":1,"status":2}', b" " * 16385])
def test_failed_or_ambiguous_public_json_is_never_reported_as_ready(body):
    assert public_observation(ORIGIN, "/healthz", execute=Mock(return_value=SimpleNamespace(stdout=body))) is None


@pytest.mark.parametrize("error", [OSError("PRIVATE-MARKER"), subprocess.TimeoutExpired("private", 12)])
def test_read_failure_returns_no_error_or_secret_body(error):
    assert public_observation(ORIGIN, "/healthz", execute=Mock(side_effect=error)) is None


@pytest.mark.parametrize(
    "origin,path,local",
    [("http://meet.example.test", "/healthz", False), (ORIGIN, "/secret", False), (ORIGIN, "/healthz", "yes")],
)
def test_invalid_target_never_invokes_a_command(origin, path, local):
    execute = Mock()
    with pytest.raises(ValueError):
        public_observation(origin, path, local_tls_route=local, execute=execute)
    execute.assert_not_called()


def test_docker_reader_requests_no_environment_mounts_keys_or_process_arguments():
    execute = Mock(
        return_value=SimpleNamespace(
            stdout=json.dumps(
                {
                    "image_id": "sha256:" + "a" * 64,
                    "revision": "b" * 40,
                    "running": True,
                    "unexpected": "PRIVATE-MARKER",
                }
            ).encode()
        )
    )
    observed = container_observation("meet-owned-1", execute=execute)
    assert observed == {"image_id": "sha256:" + "a" * 64, "revision": "b" * 40, "running": True}
    args = execute.call_args.args[0]
    assert args[:3] == ["docker", "inspect", "--type=container"]
    assert args[-1] == "meet-owned-1" and "Config.Env" not in args[-2]
    assert execute.call_args.kwargs["timeout"] == 5


@pytest.mark.parametrize("name", ["--all", "meet name", "", None])
def test_invalid_container_never_executes(name):
    execute = Mock()
    with pytest.raises(ValueError, match="container_invalid"):
        container_observation(name, execute=execute)
    execute.assert_not_called()


@pytest.mark.parametrize("admission", [False, True, "true", 1, None])
def test_report_separates_public_checks_from_trust_project_and_external_claims(admission):
    report = readiness_report(
        ORIGIN,
        {"status": "ok", "rooms": 0, "participants": True, "private": "PRIVATE-MARKER"},
        {
            "auth": {"mode": "required", "issuer": "PRIVATE-MARKER"},
            "mediaE2ee": {"mode": "required"},
            "turnConfigured": True,
            "iceServers": [{"credential": "PRIVATE-MARKER"}],
        },
        {"schema": "ananta.meet-capabilities.v1", "admissionEnabled": admission},
        {"image_id": "PRIVATE-MARKER", "revision": "PRIVATE-MARKER", "Env": ["PRIVATE-MARKER"]},
        local_tls_route=True,
    )
    assert report["status"] == ("observed" if admission is True else "blocked")
    assert report["route"] == "local-tls-hairpin"
    assert report["occupancy_snapshot"] == {"rooms": 0, "participants": None}
    assert report["production_release_eligible"] is False
    assert "current_hub_project_preauthorization" in report["unverified"]
    assert "independent_external_receiver" in report["unverified"]
    assert "PRIVATE-MARKER" not in json.dumps(report)


def test_missing_or_wrong_shape_observations_produce_bounded_blocked_report():
    report = readiness_report(ORIGIN, None, {"auth": [], "mediaE2ee": "private"}, [], None)
    assert report["status"] == "blocked" and not any(report["checks"].values())
    assert report["container"] is None


def test_cli_is_read_only_and_reports_fixed_invalid_input(monkeypatch, capsys):
    from scripts.check_meet_live_readiness import main

    container, public = Mock(side_effect=ValueError("PRIVATE-MARKER")), Mock()
    monkeypatch.setattr("scripts.check_meet_live_readiness.container_observation", container)
    monkeypatch.setattr("scripts.check_meet_live_readiness.public_observation", public)
    assert main(["--container", "bad"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "meet_readiness_input_invalid"
    public.assert_not_called()
