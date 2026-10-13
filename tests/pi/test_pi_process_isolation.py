"""Worker Pi launch profiles with real Linux confinement and synthetic SDK code."""

import json
import os
import shutil
import sys
from pathlib import Path
from threading import Event, Timer

import pytest

from agent.cli_backends.coding_agent_process import BoundedCodingAgentProcess
from worker.runtime.native_graph import pi_process


@pytest.fixture
def isolated_pi(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    secret = private / "results.sqlite3"
    secret.write_text("synthetic-private-result-canary")
    secret.chmod(0o600)
    monkeypatch.setenv("ANANTA_NATIVE_RESULT_STORE_PATH", str(secret))
    root = tmp_path / "ananta-pi-synthetic"
    workspace, config = root / "work", root / "config"
    workspace.mkdir(parents=True, mode=0o700)
    config.mkdir(mode=0o700)
    (config / "models.json").write_text('"synthetic-bound-model"')
    (config / "models.json").chmod(0o400)
    entry = tmp_path / "entry" / "pi_sdk_entry.mjs"
    entry.parent.mkdir()
    entry.with_name("pi_http_transport.mjs").write_text("// explicit transport resource fixture")
    entry.with_name("pi_runtime_credentials.mjs").write_text("// explicit credential resource fixture")
    monkeypatch.setattr(pi_process, "_sdk_entry", lambda: entry)
    sdk = tmp_path / "runtime" / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "index.js"
    sdk.parent.mkdir(parents=True)
    sdk.write_text("// explicit SDK resource fixture")
    return workspace, config, entry, sdk, secret


def run_sdk(scope, code, **options):
    workspace, config, entry, sdk, _secret = scope
    entry.write_text(code)
    return BoundedCodingAgentProcess(launch_context=pi_process.pi_execution_launch).run(
        (sys.executable, str(entry), str(sdk), str(config)), cwd=workspace,
        environment={"PATH": os.defpath, "PI_CODING_AGENT_DIR": str(config),
                     "ANANTA_PI_API_KEY": "synthetic-provider-key", "AGENT_TOKEN": "synthetic-hub-token",
                     "LD_PRELOAD": "/tmp/attack.so"},
        timeout_seconds=options.pop("timeout_seconds", 5), cancellation=Event(), maximum_output_chars=4096,
        **options,
    )


def test_sdk_reads_selected_config_and_credential_but_cannot_access_private_store(isolated_pi):
    _workspace, config, _entry, _sdk, secret = isolated_pi
    result = run_sdk(isolated_pi, f"""
import json, os, sys
from pathlib import Path
assert json.loads(Path({str(config / 'models.json')!r}).read_text()) == 'synthetic-bound-model'
assert os.environ['ANANTA_PI_API_KEY'] == 'synthetic-provider-key'
assert 'AGENT_TOKEN' not in os.environ and 'LD_PRELOAD' not in os.environ
assert sys.stdin.read() == 'synthetic delegated prompt\\n'
try:
    Path({str(secret)!r}).read_text()
except PermissionError:
    pass
else:
    raise AssertionError('private_store_escape')
try:
    Path({str(config / 'models.json')!r}).write_text('changed')
except PermissionError:
    pass
else:
    raise AssertionError('config_mutation')
print('bounded_sdk_completed')
""", input_text="synthetic delegated prompt\n")
    assert result.return_code == 0, result.stderr
    assert result.stdout == "bounded_sdk_completed\n"
    assert secret.read_text() == "synthetic-private-result-canary"


def test_sdk_cancellation_and_output_bounds_survive_launcher(isolated_pi):
    result = run_sdk(isolated_pi, "import time\ntime.sleep(3)", timeout_seconds=0.2)
    assert result.return_code == 124 and result.reason_code == "timeout" and result.duration_ms < 2000
    result = run_sdk(isolated_pi, "print('x' * 10000)")
    assert result.reason_code == "output_limit_exceeded" and result.output_truncated


def test_sdk_can_read_system_dns_configuration_but_not_private_results(isolated_pi):
    secret = isolated_pi[-1]
    result = run_sdk(isolated_pi, f"""
from pathlib import Path
for filename in ['/etc/hosts', '/etc/resolv.conf']:
    path = Path(filename)
    if path.exists():
        path.read_bytes()
try:
    Path({str(secret)!r}).read_bytes()
except PermissionError:
    pass
else:
    raise AssertionError('private_store_escape')
print('dns_configuration_readable_private_store_denied')
""")
    assert result.return_code == 0, result.stderr
    assert result.stdout == 'dns_configuration_readable_private_store_denied\n'


def test_sdk_cancellation_stops_guarded_child(isolated_pi):
    cancellation = Event()
    timer = Timer(0.2, cancellation.set)
    timer.start()
    try:
        workspace, config, entry, sdk, _secret = isolated_pi
        entry.write_text("import time\ntime.sleep(5)")
        result = BoundedCodingAgentProcess(launch_context=pi_process.pi_execution_launch).run(
            (sys.executable, str(entry), str(sdk), str(config)), cwd=workspace,
            environment={"PATH": os.defpath, "PI_CODING_AGENT_DIR": str(config)},
            timeout_seconds=3, cancellation=cancellation, maximum_output_chars=1024,
        )
        assert result.reason_code == "cancelled" and result.duration_ms < 2000
    finally:
        timer.cancel()


def test_foreign_configuration_or_package_layout_never_launches(isolated_pi, monkeypatch):
    workspace, config, entry, sdk, _secret = isolated_pi
    entry.write_text("raise AssertionError('must not launch')")
    monkeypatch.setattr("agent.cli_backends.coding_agent_process.subprocess.Popen",
                        lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(PermissionError, match="configuration_binding"):
        BoundedCodingAgentProcess(launch_context=pi_process.pi_execution_launch).run(
            (sys.executable, str(entry), str(sdk), str(config)), cwd=workspace,
            environment={"PI_CODING_AGENT_DIR": "/foreign"}, timeout_seconds=1,
            cancellation=Event(), maximum_output_chars=1024,
        )


def test_probe_is_disposable_and_never_receives_provider_or_service_keys(isolated_pi):
    _workspace, _config, _entry, _sdk, secret = isolated_pi
    binary = secret.parent.parent / "synthetic-probe"
    binary.write_text(f"""#!{sys.executable}
import os
from pathlib import Path
assert 'ANANTA_PI_API_KEY' not in os.environ and 'AGENT_TOKEN' not in os.environ
assert Path.cwd().name.startswith('ananta-worker-probe-')
try:
    Path({str(secret)!r}).read_text()
except PermissionError:
    print('probe_isolated')
else:
    raise AssertionError('probe_escape')
""")
    binary.chmod(0o700)
    result = pi_process.PiProbeProcess().run(
        (str(binary), "--version"), cwd=binary.parent,
        environment={"PATH": os.defpath, "ANANTA_PI_API_KEY": "synthetic-provider", "AGENT_TOKEN": "synthetic-hub"},
        timeout_seconds=5, cancellation=Event(), maximum_output_chars=4096,
    )
    assert result.return_code == 0 and result.stdout == "probe_isolated\n", result.stderr


def test_default_worker_provider_injects_both_protected_runners(monkeypatch):
    captured = {}
    monkeypatch.setattr(pi_process, "PiCodingAgentProvider", lambda **options: captured.update(options) or options)
    pi_process.build_worker_pi_provider(enabled=True)
    assert captured["process_runner"]._launch is pi_process.pi_execution_launch
    fake = type("Provisioner", (), {"status": lambda self, name: name})()
    monkeypatch.setattr(pi_process, "CliBackendProvisioner", lambda **options: captured.update(options) or fake)
    assert captured["runtime_probe"]() == "pi"
    assert isinstance(captured["headless_runner"], pi_process.PiProbeProcess)


@pytest.fixture
def node_binary():
    binary = os.environ.get("ANANTA_TEST_NODE_BINARY") or shutil.which("node")
    if not binary:
        pytest.skip("Node runtime unavailable; use an explicit cached test binary")
    return str(Path(binary).resolve(strict=True))


def test_real_node_reads_sdk_resources_but_not_private_store(isolated_pi, node_binary):
    workspace, config, entry, sdk, secret = isolated_pi
    entry.write_text(f"""
import {{ readFileSync, writeFileSync }} from 'node:fs';
import assert from 'node:assert/strict';
assert.equal(readFileSync({json.dumps(str(config / 'models.json'))}, 'utf8'), '"synthetic-bound-model"');
assert.equal(process.env.ANANTA_PI_API_KEY, 'synthetic-provider');
assert.equal(process.env.AGENT_TOKEN, undefined);
assert.equal(readFileSync(0, 'utf8'), 'delegated prompt');
assert.throws(() => readFileSync({json.dumps(str(secret))}), {{ code: 'EACCES' }});
assert.throws(() => writeFileSync({json.dumps(str(config / 'models.json'))}, 'changed'), {{ code: 'EACCES' }});
writeFileSync('delegated-output.txt', 'workspace writable');
console.log('node_sdk_isolated');
""")
    result = BoundedCodingAgentProcess(launch_context=pi_process.pi_execution_launch).run(
        (node_binary, str(entry), str(sdk), str(config)), cwd=workspace,
        environment={"PATH": str(Path(node_binary).parent), "PI_CODING_AGENT_DIR": str(config),
                     "ANANTA_PI_API_KEY": "synthetic-provider", "AGENT_TOKEN": "synthetic-hub"},
        input_text="delegated prompt", timeout_seconds=5, cancellation=Event(), maximum_output_chars=4096,
    )
    assert result.return_code == 0, result.stderr
    assert result.stdout == "node_sdk_isolated\n"
    assert (workspace / "delegated-output.txt").read_text() == "workspace writable"


def test_real_node_cli_probe_uses_catalog_bundle_layout(isolated_pi, node_binary):
    _workspace, _config, _entry, sdk, secret = isolated_pi
    binary = sdk.parent / "bundle" / "cli.js"
    binary.parent.mkdir()
    (sdk.parents[1] / "package.json").write_text('{"type":"module"}')
    binary.write_text(f"""#!{node_binary}
import {{ readFileSync }} from 'node:fs';
import assert from 'node:assert/strict';
assert.equal(process.argv[2], '--version');
assert.equal(process.env.ANANTA_PI_API_KEY, undefined);
assert.equal(process.env.AGENT_TOKEN, undefined);
assert.match(process.cwd(), /ananta-worker-probe-/);
assert.throws(() => readFileSync({json.dumps(str(secret))}), {{ code: 'EACCES' }});
console.log('0.85.1');
""")
    binary.chmod(0o700)
    result = pi_process.PiProbeProcess().run(
        (str(binary), "--version"), cwd=binary.parent,
        environment={"PATH": str(Path(node_binary).parent), "ANANTA_PI_API_KEY": "synthetic-provider",
                     "AGENT_TOKEN": "synthetic-hub"},
        timeout_seconds=5, cancellation=Event(), maximum_output_chars=4096,
    )
    assert result.return_code == 0, result.stderr
    assert result.stdout == "0.85.1\n"
