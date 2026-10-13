"""Worker-only Pi execution/probe composition over the bounded process port."""

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import agent.cli_backends.pi_runtime as pi_runtime
from agent.cli_backends.coding_agent_process import BoundedCodingAgentProcess
from agent.cli_backends.pi_provider import PiCodingAgentProvider
from agent.cli_backends.provisioning import CliBackendProvisioner
from worker.shell.filesystem_isolation import configured_private_result_path, isolated_command

_PI_ENVIRONMENT = ("CI", "NO_COLOR", "PI_OFFLINE", "PI_TELEMETRY", "PI_CODING_AGENT_DIR", "ANANTA_PI_API_KEY")


def _sdk_entry():
    return Path(pi_runtime.__file__).with_name("pi_sdk_entry.mjs").resolve(strict=True)


def _package_resources(sdk):
    sdk = Path(sdk).resolve(strict=True)
    if sdk.name != "index.js" or sdk.parent.name != "dist":
        raise PermissionError("pi_filesystem_runtime_layout_invalid")
    package = sdk.parent.parent
    modules = package.parent.parent
    if (package.name != "pi-coding-agent" or package.parent.name != "@earendil-works"
            or modules.name != "node_modules"):
        raise PermissionError("pi_filesystem_runtime_layout_invalid")
    return modules


@contextmanager
def pi_execution_launch(argv, *, workspace, environment):
    if not configured_private_result_path():
        yield argv, environment, ()
        return
    root = Path(workspace).resolve(strict=True)
    config = (root.parent / "config").resolve(strict=True)
    if (len(argv) != 4 or Path(argv[1]).resolve(strict=True) != _sdk_entry()
            or root.name != "work" or not root.parent.name.startswith("ananta-pi-")
            or config != root.parent / "config" or Path(argv[3]).resolve(strict=True) != config
            or environment.get("PI_CODING_AGENT_DIR") != str(config)):
        raise PermissionError("pi_filesystem_configuration_binding_invalid")
    entry = _sdk_entry()
    resources = (config, entry, entry.with_name("pi_http_transport.mjs").resolve(strict=True),
                 entry.with_name("pi_runtime_credentials.mjs").resolve(strict=True),
                 _package_resources(argv[2]), Path(argv[0]).resolve(strict=True))
    # The Hub may select a Docker service hostname. Grant only these fixed
    # system DNS files, never the whole /etc tree or Worker-private state.
    resources += tuple(Path(name).resolve(strict=True) for name in ("/etc/hosts", "/etc/resolv.conf")
                       if Path(name).is_file())
    with isolated_command(argv, workspace=root, environment=environment, readonly_paths=resources,
                          allowed_environment_keys=_PI_ENVIRONMENT) as launch:
        yield launch


class PiProbeProcess:
    """Version probes use disposable cwd, no provider credentials or writable installation."""

    def run(self, argv, *, cwd, environment, **options):
        if not configured_private_result_path():
            return BoundedCodingAgentProcess().run(argv, cwd=cwd, environment=environment, **options)
        if len(argv) != 2 or argv[1] != "--version":
            raise PermissionError("pi_filesystem_probe_scope_invalid")
        binary = Path(argv[0]).resolve(strict=True)
        resources = [binary]
        if binary.name == "cli.js":
            resources.append(_package_resources(binary.parent.parent / "index.js"))
            node = shutil.which("node", path=environment.get("PATH", os.defpath))
            if not node:
                raise PermissionError("pi_filesystem_node_runtime_unavailable")
            resources.append(Path(node).resolve(strict=True))
        with TemporaryDirectory(prefix="ananta-worker-probe-") as directory:
            workspace = Path(directory)
            # No inherited agent/config directory is authoritative for a probe.
            projected = {**environment, "PI_CODING_AGENT_DIR": str(workspace)}

            def launch(args, *, workspace, environment):
                return isolated_command(args, workspace=workspace, environment=environment, readonly_paths=resources,
                                        allowed_environment_keys=_PI_ENVIRONMENT[:-1])

            return BoundedCodingAgentProcess(launch_context=launch).run(
                argv, cwd=workspace, environment=projected, **options,
            )


def build_worker_pi_provider(**options):
    return PiCodingAgentProvider(
        process_runner=BoundedCodingAgentProcess(launch_context=pi_execution_launch),
        runtime_probe=lambda: CliBackendProvisioner(headless_runner=PiProbeProcess()).status("pi"),
        **options,
    )
