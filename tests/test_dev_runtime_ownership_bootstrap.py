from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/bootstrap-dev-runtime-ownership.py"
COMPOSE = ROOT / "docker/compose-next/compose.dev.ollama.yml"
BASE_COMPOSE = ROOT / "docker/compose-next/compose.base.yml"
ENTRYPOINT = ROOT / "scripts/quickstart-single-image-entrypoint.sh"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "bootstrap_dev_runtime_ownership",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    data_root = tmp_path / "data"
    workspace_root = tmp_path / "workspaces"
    credential_root = tmp_path / "credentials"
    for name in ("alpha", "beta", "frontend-angular-cache", "hub"):
        service_root = data_root / name
        service_root.mkdir(parents=True)
        (service_root / "state.json").write_text("{}", encoding="utf-8")
    workspace_root.mkdir()
    credential_root.mkdir()
    private_directory = credential_root / "alpha"
    private_directory.mkdir()
    (private_directory / "service-token").write_text(
        "test-only-token",
        encoding="utf-8",
    )
    return data_root, workspace_root, credential_root


def _non_root_test_owner() -> tuple[int, int]:
    return os.getuid() or 1000, os.getgid() or 1000


def test_runtime_ownership_bootstrap_prepares_only_fixed_service_roots(tmp_path):
    module = _load_script()
    data_root, workspace_root, credential_root = _runtime_tree(tmp_path)
    owner_uid, owner_gid = _non_root_test_owner()

    module.prepare_runtime_ownership(
        data_root,
        workspace_root,
        credential_root,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )

    for path in (workspace_root, credential_root, *data_root.rglob("*")):
        assert path.stat().st_uid == owner_uid
        assert path.stat().st_gid == owner_gid

    unchanged_ctimes = {
        path: path.stat().st_ctime_ns
        for path in (workspace_root, credential_root, *data_root.rglob("*"))
    }
    module.prepare_runtime_ownership(
        data_root,
        workspace_root,
        credential_root,
        owner_uid=owner_uid,
        owner_gid=owner_gid,
    )
    assert {
        path: path.stat().st_ctime_ns
        for path in unchanged_ctimes
    } == unchanged_ctimes


def test_runtime_ownership_bootstrap_rejects_symlinked_data_root(tmp_path):
    module = _load_script()
    data_root, workspace_root, credential_root = _runtime_tree(tmp_path)
    owner_uid, owner_gid = _non_root_test_owner()
    linked_root = tmp_path / "linked-data"
    linked_root.symlink_to(data_root, target_is_directory=True)

    with pytest.raises(
        module.RuntimeOwnershipBootstrapError,
        match="must be a real directory",
    ):
        module.prepare_runtime_ownership(
            linked_root,
            workspace_root,
            credential_root,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
        )


def test_runtime_ownership_bootstrap_rejects_special_data_entries(tmp_path):
    module = _load_script()
    data_root, workspace_root, credential_root = _runtime_tree(tmp_path)
    owner_uid, owner_gid = _non_root_test_owner()
    os.mkfifo(data_root / "hub" / "unsafe-fifo")

    with pytest.raises(
        module.RuntimeOwnershipBootstrapError,
        match="unsupported entry",
    ):
        module.prepare_runtime_ownership(
            data_root,
            workspace_root,
            credential_root,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
        )


def test_runtime_ownership_bootstrap_rejects_linked_credentials(tmp_path):
    module = _load_script()
    data_root, workspace_root, credential_root = _runtime_tree(tmp_path)
    owner_uid, owner_gid = _non_root_test_owner()
    credential = credential_root / "alpha" / "service-token"
    (credential_root / "alpha" / "service-token-alias").symlink_to(
        credential.name
    )

    with pytest.raises(
        module.RuntimeOwnershipBootstrapError,
        match="symbolic link",
    ):
        module.prepare_runtime_ownership(
            data_root,
            workspace_root,
            credential_root,
            owner_uid=owner_uid,
            owner_gid=owner_gid,
        )


def test_assign_if_needed_changes_a_mismatched_owner(monkeypatch, tmp_path):
    module = _load_script()
    target = tmp_path / "state"
    target.write_text("{}", encoding="utf-8")
    calls: list[tuple[Path, int, int, bool]] = []

    monkeypatch.setattr(
        module.os,
        "chown",
        lambda path, uid, gid, *, follow_symlinks: calls.append(
            (Path(path), uid, gid, follow_symlinks)
        ),
    )

    module._assign_if_needed(
        target,
        SimpleNamespace(st_uid=0, st_gid=0),
        owner_uid=1000,
        owner_gid=1000,
    )

    assert calls == [(target, 1000, 1000, False)]


def test_assign_if_needed_rejects_an_unexpected_owner(tmp_path):
    module = _load_script()
    target = tmp_path / "state"
    target.write_text("{}", encoding="utf-8")

    with pytest.raises(
        module.RuntimeOwnershipBootstrapError,
        match="unexpected owner",
    ):
        module._assign_if_needed(
            target,
            SimpleNamespace(st_uid=2000, st_gid=2000),
            owner_uid=1000,
            owner_gid=1000,
        )


def test_ollama_compose_runs_python_services_as_the_credential_owner():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    base_compose = yaml.safe_load(BASE_COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    bootstrap = services["runtime-data-bootstrap"]
    expected_build_args = {
        "ANANTA_RUNTIME_UID": "${ANANTA_HOST_UID:-1000}",
        "ANANTA_RUNTIME_GID": "${ANANTA_HOST_GID:-1000}",
    }

    assert bootstrap["user"] == "0:0"
    assert bootstrap["build"]["args"] == expected_build_args
    assert bootstrap["network_mode"] == "none"
    assert bootstrap["read_only"] is True
    assert bootstrap["cap_drop"] == ["ALL"]
    assert set(bootstrap["cap_add"]) == {"CHOWN", "DAC_READ_SEARCH"}
    assert bootstrap["security_opt"] == ["no-new-privileges:true"]
    assert bootstrap["entrypoint"] == [
        "python",
        "/app/scripts/bootstrap-dev-runtime-ownership.py",
    ]
    assert bootstrap["command"][-4:] == [
        "--owner-uid",
        "${ANANTA_HOST_UID:-1000}",
        "--owner-gid",
        "${ANANTA_HOST_GID:-1000}",
    ]
    keyring_bootstrap = services["workflow-keyring-bootstrap"]
    assert keyring_bootstrap["build"]["args"] == expected_build_args
    assert keyring_bootstrap["user"] == (
        "${ANANTA_HOST_UID:-1000}:${ANANTA_HOST_GID:-1000}"
    )
    assert keyring_bootstrap["network_mode"] == "none"
    assert keyring_bootstrap["read_only"] is True
    assert keyring_bootstrap["cap_drop"] == ["ALL"]
    assert keyring_bootstrap["security_opt"] == ["no-new-privileges:true"]
    assert (
        keyring_bootstrap["depends_on"]["runtime-data-bootstrap"]["condition"]
        == "service_completed_successfully"
    )

    for name in ("ai-agent-hub", "ai-agent-alpha", "ai-agent-beta"):
        service = services[name]
        assert service["user"] == (
            "${ANANTA_HOST_UID:-1000}:${ANANTA_HOST_GID:-1000}"
        )
        assert service["environment"]["HOME"] == "/app/data/home"
        assert service["environment"]["XDG_CACHE_HOME"] == "/app/data/cache"
        assert (
            service["depends_on"]["runtime-data-bootstrap"]["condition"]
            == "service_completed_successfully"
        )

    frontend = services["angular-frontend"]
    assert frontend["user"] == (
        "${ANANTA_HOST_UID:-1000}:${ANANTA_HOST_GID:-1000}"
    )
    assert frontend["environment"]["HOME"] == (
        "/app/frontend-angular/.angular/home"
    )
    assert frontend["environment"]["XDG_CACHE_HOME"] == (
        "/app/frontend-angular/.angular/cache"
    )
    assert (
        frontend["depends_on"]["runtime-data-bootstrap"]["condition"]
        == "service_completed_successfully"
    )
    assert (
        "frontend-angular-cache:/run/ananta-dev-data/frontend-angular-cache"
        in bootstrap["volumes"]
    )

    for name in (
        "ai-agent-hub-base",
        "ai-agent-worker-base",
        "angular-frontend-base",
    ):
        assert base_compose["services"][name]["build"]["args"] == (
            expected_build_args
        )


def test_runtime_entrypoint_does_not_install_test_packages():
    source = ENTRYPOINT.read_text(encoding="utf-8")

    assert "ensure_pytest_available" not in source
    assert "pip install" not in source
    assert "Refusing symlinked runtime log" in source
    assert 'chmod 0600 "$runtime_file"' in source
    assert 'mkdir -p "${XDG_CACHE_HOME}"' in source
