"""Pinned Pi installation stays bounded, headless and credential-isolated."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.cli_backends.coding_agent_contract import ProcessExecutionResult
from agent.cli_backends.provisioning import CliBackendProvisioner, CliBackendProvisioningError


class ProvisioningRunner:
    def __init__(self, *, node="v24.18.0\n", version="0.85.1\n", install_rc=0):
        self.node = node
        self.version = version
        self.install_rc = install_rc
        self.calls = []
        self.configurations = []

    def run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        assert kwargs["maximum_output_chars"] == 32_768
        assert not kwargs["cancellation"].is_set()
        assert kwargs.get("input_text") is None
        if argv[0] == "/usr/bin/node":
            assert kwargs["timeout_seconds"] == 5
            return ProcessExecutionResult(0, self.node, "", "completed", 1)
        if argv[0] == "/usr/bin/npm":
            assert kwargs["timeout_seconds"] == 540
            prefix = Path(argv[argv.index("--prefix") + 1])
            assert kwargs["cwd"] == prefix
            assert argv == [
                "/usr/bin/npm", "install", "--prefix", str(prefix), "--ignore-scripts",
                "--no-audit", "--no-fund", "--save-exact", "@earendil-works/pi-coding-agent@0.85.1",
            ]
            environment = kwargs["environment"]
            configs = [Path(environment[key]) for key in ("NPM_CONFIG_USERCONFIG", "NPM_CONFIG_GLOBALCONFIG")]
            assert configs[0] != configs[1]
            for config in configs:
                assert config.read_text() == ""
                assert config.stat().st_mode & 0o777 == 0o400
            self.configurations.extend(configs)
            binary = prefix / "node_modules" / ".bin" / "pi"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(0o700)
            return ProcessExecutionResult(self.install_rc, "", "", "completed", 1)
        assert argv[1:] == ["--version"]
        assert kwargs["timeout_seconds"] == 5
        configuration = Path(kwargs["environment"]["PI_CODING_AGENT_DIR"])
        assert configuration.is_dir() and configuration.stat().st_mode & 0o777 == 0o700
        self.configurations.append(configuration)
        return ProcessExecutionResult(0, self.version, "", "completed", 1)


@pytest.fixture
def node_tools(monkeypatch):
    monkeypatch.setattr(
        "agent.cli_backends.headless_node_provisioning.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"node", "npm"} else None,
    )


def test_pi_install_uses_pinned_package_and_private_npm_configuration(tmp_path, monkeypatch, node_tools):
    for key in ("AGENT_TOKEN", "OPENAI_API_KEY", "NODE_OPTIONS", "NPM_CONFIG_USERCONFIG"):
        monkeypatch.setenv(key, "ambient-value-must-not-reach-command")
    runner = ProvisioningRunner()
    provisioner = CliBackendProvisioner(base_dir=tmp_path, headless_runner=runner)

    result = provisioner.install("pi")

    assert result["status"] == "ready"
    assert result["version_probe"] == {"rc": 0, "stdout": "0.85.1", "stderr": ""}
    assert len(runner.calls) == 4
    assert all(not config.exists() for config in runner.configurations)
    for _, arguments in runner.calls:
        environment = arguments["environment"]
        assert "ambient-value-must-not-reach-command" not in environment.values()
        assert environment["PI_OFFLINE"] == "1"
        assert environment["PI_TELEMETRY"] == "0"
        assert environment["CI"] == "1"


def test_pi_uninstalled_status_has_no_process_or_directory_side_effect(tmp_path):
    runner = ProvisioningRunner()
    provisioner = CliBackendProvisioner(base_dir=tmp_path / "absent", headless_runner=runner)
    assert provisioner.status("pi")["status"] == "not_installed"
    assert runner.calls == []
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("version", ["v22.18.9", "v20.99.0", "garbage", "v24.18.0\nextra", "v99999.0.0"])
def test_pi_rejects_incompatible_node_before_install(tmp_path, node_tools, version):
    runner = ProvisioningRunner(node=version)
    provisioner = CliBackendProvisioner(base_dir=tmp_path, headless_runner=runner)
    with pytest.raises(CliBackendProvisioningError, match="^node_runtime_incompatible$"):
        provisioner.install("pi")
    assert len(runner.calls) == 1
    assert not provisioner.install_prefix("pi").exists()


@pytest.mark.parametrize("missing,reason", [("npm", "npm_not_available"), ("node", "node_runtime_unavailable")])
def test_pi_missing_runtime_is_closed_error(tmp_path, monkeypatch, missing, reason):
    monkeypatch.setattr(
        "agent.cli_backends.headless_node_provisioning.shutil.which",
        lambda name: None if name == missing else f"/usr/bin/{name}",
    )
    provisioner = CliBackendProvisioner(base_dir=tmp_path, headless_runner=ProvisioningRunner())
    with pytest.raises(CliBackendProvisioningError, match=f"^{reason}$"):
        provisioner.install("pi")


@pytest.mark.parametrize("return_code", [1, 65, 124, 130])
def test_pi_install_failure_cleans_configuration_and_never_reports_ready(tmp_path, node_tools, return_code):
    runner = ProvisioningRunner(install_rc=return_code)
    provisioner = CliBackendProvisioner(base_dir=tmp_path, headless_runner=runner)
    with pytest.raises(CliBackendProvisioningError, match="^npm_install_failed$"):
        provisioner.install("pi")
    assert len(runner.calls) == 2
    assert all(not config.exists() for config in runner.configurations)


@pytest.mark.parametrize("version", ["0.85.2", "0.85.1\nextra", "", "secret-token"])
def test_pi_install_rejects_unverified_version_without_projecting_raw_output(tmp_path, node_tools, version):
    runner = ProvisioningRunner(version=version)
    provisioner = CliBackendProvisioner(base_dir=tmp_path, headless_runner=runner)
    with pytest.raises(CliBackendProvisioningError, match="^installed_binary_verification_failed$"):
        provisioner.install("pi")
    status = provisioner.status("pi")
    assert status["status"] == "error"
    assert status["version_probe"] == {"rc": 1, "stdout": "", "stderr": "node_package_version_unverified"}


@pytest.mark.parametrize("exception", [OSError("private diagnostic"), ValueError("private diagnostic")])
def test_pi_process_failure_is_closed_diagnostic(tmp_path, node_tools, exception):
    runner = MagicMock()
    runner.run.side_effect = exception
    provisioner = CliBackendProvisioner(base_dir=tmp_path, headless_runner=runner)
    with pytest.raises(CliBackendProvisioningError, match="^node_package_command_failed$"):
        provisioner.install("pi")


@pytest.mark.parametrize("endpoint", ["worker-action", "account-login"])
def test_pi_does_not_inherit_interactive_login_routes(client, admin_auth_header, monkeypatch, endpoint):
    monkeypatch.setattr("agent.routes.sgpt.settings.role", "worker")
    response = client.post(
        f"/api/sgpt/backends/pi/{endpoint}",
        json={"action": "login_start"}, headers=admin_auth_header,
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "reason", ["node_runtime_unavailable", "node_runtime_incompatible", "node_package_command_failed"]
)
def test_pi_provision_route_reports_bounded_runtime_failure(client, admin_auth_header, monkeypatch, reason):
    provisioner = MagicMock()
    provisioner.install.side_effect = CliBackendProvisioningError(reason)
    monkeypatch.setattr("agent.routes.sgpt.settings.role", "worker")
    monkeypatch.setattr("agent.cli_backends.provisioning.get_cli_backend_provisioner", lambda: provisioner)
    response = client.post(
        "/api/sgpt/backends/pi/provision", json={"action": "install"}, headers=admin_auth_header,
    )
    assert response.status_code == 500
    assert response.json["data"]["reason_code"] == reason
