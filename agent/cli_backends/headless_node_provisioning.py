"""Bounded commands for explicitly catalogued, noninteractive Node packages."""

import os
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from agent.cli_backends.coding_agent_contract import ProcessRunnerPort
from agent.cli_backends.coding_agent_process import BoundedCodingAgentProcess


class HeadlessNodeProvisioningError(RuntimeError):
    pass


class HeadlessNodeProvisioning:
    def __init__(self, runner: ProcessRunnerPort | None = None):
        self.runner = runner if runner is not None else BoundedCodingAgentProcess()

    def install(self, package: str, version: str, prefix: Path, minimum_node: tuple[int, int, int]):
        npm = shutil.which("npm")
        if not npm:
            raise HeadlessNodeProvisioningError("npm_not_available")
        self._check_node(minimum_node)
        prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
        with TemporaryDirectory(prefix=".npm-policy-", dir=prefix) as directory:
            configuration = Path(directory)
            # npm rejects loading the same /dev/null path as both config types.
            for name in ("user", "global"):
                with (configuration / name).open("x", encoding="utf-8"):
                    pass
                (configuration / name).chmod(0o400)
            result = self._run(
                [npm, "install", "--prefix", str(prefix), "--ignore-scripts", "--no-audit", "--no-fund",
                 "--save-exact", f"{package}@{version}"],
                cwd=prefix, timeout=540, npm_configuration=configuration,
            )
        if result.returncode != 0:
            raise HeadlessNodeProvisioningError("npm_install_failed")

    def probe(self, binary: Path, version: str, minimum_node: tuple[int, int, int]) -> subprocess.CompletedProcess[str]:
        self._check_node(minimum_node)
        with TemporaryDirectory(prefix="ananta-node-version-") as directory:
            result = self._run(
                [str(binary), "--version"], cwd=binary.parent, timeout=5, agent_directory=Path(directory),
            )
        valid = result.returncode == 0 and result.stdout.strip() == version and not result.stderr.strip()
        return subprocess.CompletedProcess(
            [str(binary), "--version"],
            0 if valid else 1,
            version if valid else "",
            "" if valid else "node_package_version_unverified",
        )

    def _check_node(self, minimum: tuple[int, int, int]):
        node = shutil.which("node")
        if not node:
            raise HeadlessNodeProvisioningError("node_runtime_unavailable")
        probe = self._run([node, "--version"], cwd=Path.cwd(), timeout=5)
        match = re.fullmatch(r"v(\d{1,4})\.(\d{1,4})\.(\d{1,4})", probe.stdout.strip())
        if probe.returncode != 0 or probe.stderr.strip() or match is None or tuple(map(int, match.groups())) < minimum:
            raise HeadlessNodeProvisioningError("node_runtime_incompatible")

    def _run(
        self, command, *, cwd: Path, timeout: int, npm_configuration: Path | None = None,
        agent_directory: Path | None = None,
    ):
        # No provider/Hub tokens or npm user/global configuration are inherited.
        environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
        environment.update({
            "CI": "1", "NO_COLOR": "1", "PI_OFFLINE": "1", "PI_TELEMETRY": "0",
        })
        if agent_directory is not None:
            environment["PI_CODING_AGENT_DIR"] = str(agent_directory)
        if npm_configuration is not None:
            environment.update({
                "NPM_CONFIG_USERCONFIG": str(npm_configuration / "user"),
                "NPM_CONFIG_GLOBALCONFIG": str(npm_configuration / "global"),
                "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org/", "NPM_CONFIG_IGNORE_SCRIPTS": "true",
                "NPM_CONFIG_CACHE": str(cwd / ".npm-cache"),
            })
        try:
            result = self.runner.run(
                command, cwd=cwd, environment=environment, timeout_seconds=timeout,
                cancellation=Event(), maximum_output_chars=32_768,
            )
        except (OSError, ValueError) as exc:
            raise HeadlessNodeProvisioningError("node_package_command_failed") from exc
        return subprocess.CompletedProcess(command, result.return_code, result.stdout, result.stderr)
