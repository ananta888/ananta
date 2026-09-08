"""Automatic private-file operator workflow with no person or serving database."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent.services.meet_preauthorization_input import read_operator_policy
from scripts.meet_dialog_preauthorization import main
from tests.test_meet_preauthorization_policy import document
from tests.test_meet_preauthorization_store import store  # noqa: F401

pytestmark = pytest.mark.timeout(30)


def policy_file(tmp_path, value=None):
    path = tmp_path / "private-policy.json"
    path.write_text(json.dumps(value or document()))
    path.chmod(0o600)
    return path


def test_injected_operator_flow_persists_exact_cas_without_logging_scope(store, tmp_path, capsys):  # noqa: F811
    path = policy_file(tmp_path)
    factory = Mock(return_value=store)
    assert main(["provision", "--policy-file", str(path), "--expected-revision", "0"], store_factory=factory) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "active" and result["revision"] == 1
    assert main(["revoke", "--policy-id", "synthetic-policy", "--expected-revision", "1"], store_factory=factory) == 0
    raw = capsys.readouterr().out
    assert json.loads(raw)["status"] == "revoked"
    assert str(path) not in raw and "room-" not in raw and document()["origin"] not in raw
    assert main(["revoke", "--policy-id", "synthetic-policy", "--expected-revision", "1"], store_factory=factory) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "meet_preauthorization_conflict"


@pytest.mark.parametrize(
    "change", ["relative", "symlink", "hardlink", "public", "directory", "large", "duplicate", "fifo"]
)
def test_invalid_private_input_fails_before_database_initialization(tmp_path, capsys, change):
    path = policy_file(tmp_path)
    if change == "relative":
        path = Path("relative-private-file")
    elif change == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(path)
        path = alias
    elif change == "hardlink":
        os.link(path, tmp_path / "alias")
    elif change == "public":
        path.chmod(0o644)
    elif change == "directory":
        path = tmp_path
    elif change == "large":
        path.write_bytes(b"x" * 4097)
    elif change == "duplicate":
        path.write_bytes(b'{"schema":"one","schema":"two"}')
    else:
        path = tmp_path / "private-fifo"
        os.mkfifo(path, 0o600)
    factory = Mock(side_effect=AssertionError("must not touch database"))
    assert main(["provision", "--policy-file", str(path), "--expected-revision", "0"], store_factory=factory) == 2
    raw = capsys.readouterr().out
    assert json.loads(raw)["status"] == "blocked" and str(path) not in raw
    factory.assert_not_called()


def test_readonly_owned_file_supported_and_unknown_fields_rejected(tmp_path):
    path = policy_file(tmp_path)
    path.chmod(0o400)
    assert read_operator_policy(path) == document()
    path.chmod(0o600)
    path.write_text(json.dumps(document() | {"implicit_approval": True}))
    with pytest.raises(ValueError):
        read_operator_policy(path)


@pytest.mark.parametrize(
    "args", [[], ["other"], ["revoke", "--policy-id", "private-secret", "--expected-revision", "bad"]]
)
def test_argument_diagnostics_never_echo_user_input(args, capsys):
    factory = Mock()
    assert main(args, store_factory=factory) == 2
    output = capsys.readouterr()
    assert "private-secret" not in output.out + output.err
    assert json.loads(output.out)["status"] == "blocked"
    factory.assert_not_called()


def test_real_cli_provision_and_revoke_survive_process_restart_and_worker_role_is_denied(tmp_path):
    now = int(time.time())
    path = policy_file(tmp_path, document() | {"valid_from": now - 1, "expires_at": now + 600})
    root = Path(__file__).resolve().parents[1]
    environment = os.environ | {"ROLE": "hub", "DATABASE_URL": "sqlite:///" + str(tmp_path / "hub-policy.sqlite")}

    def invoke(args, *, role="hub"):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.meet_dialog_preauthorization", *args],
            cwd=root,
            env=environment | {"ROLE": role},
            capture_output=True,
            text=True,
            timeout=12,
        )
        assert str(path) not in result.stdout + result.stderr and "room-" not in result.stdout + result.stderr
        return result.returncode, json.loads(result.stdout)

    provision = ["provision", "--policy-file", str(path), "--expected-revision", "0"]
    code, blocked = invoke(provision, role="worker")
    assert code == 2 and blocked["code"] == "meet_preauthorization_hub_required"
    assert not (tmp_path / "hub-policy.sqlite").exists()
    code, created = invoke(provision)
    assert code == 0 and created["revision"] == 1
    code, revoked = invoke(["revoke", "--policy-id", "synthetic-policy", "--expected-revision", "1"])
    assert code == 0 and revoked["status"] == "revoked" and revoked["revision"] == 2
