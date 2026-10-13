"""Real unprivileged kernel boundaries; all commands bounded and headless."""

import json
import shlex
import sys

import pytest
from flask import Flask

from worker.shell.command_executor import execute_command_plan
from worker.shell.filesystem_isolation import isolated_command
from worker.shell.landlock_kernel import LandlockKernel


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    secret = private / "results.sqlite3"
    secret.write_text("synthetic-private-canary")
    secret.chmod(0o600)
    monkeypatch.setenv("ANANTA_NATIVE_RESULT_STORE_PATH", str(secret))
    return workspace, secret


def execute(isolated, code, *, environment=None):
    workspace, _secret = isolated
    return execute_command_plan(
        repository_root=workspace,
        command_plan_artifact={"command": shlex.join([sys.executable, "-c", code])},
        task_id="synthetic-task", capability_id="shell_execute", context_hash="synthetic-context",
        shell_policy={"allowlist": [sys.executable]}, hub_policy_decision="allow",
        timeout_seconds=10, environment=environment,
    )


def test_tools_write_workspace_but_cannot_read_write_or_truncate_private_store(isolated):
    _workspace, secret = isolated
    result = execute(isolated, f"""
import json, os
from pathlib import Path
Path('allowed.txt').write_text('allowed')
blocked = []
for operation in [lambda: Path({str(secret)!r}).read_text(),
                  lambda: Path({str(secret)!r}).write_text('tampered'),
                  lambda: os.truncate({str(secret)!r}, 0)]:
    try:
        operation()
    except PermissionError:
        blocked.append(True)
    else:
        blocked.append(False)
print(json.dumps(blocked))
""")
    assert result.success, result.stderr
    assert json.loads(result.stdout) == [True, True, True]
    assert secret.read_text() == "synthetic-private-canary"
    assert (_workspace / "allowed.txt").read_text() == "allowed"


def test_descriptor_ioctl_exceptions_do_not_allow_inode_or_device_operations(isolated):
    result = execute(isolated, """
import errno, fcntl, os, struct
reader, writer = os.pipe()
fcntl.ioctl(reader, 0x5421, struct.pack('i', 1))
assert os.get_blocking(reader) is False
fcntl.ioctl(reader, 0x5451)
assert os.get_inheritable(reader) is False
with open('inode.txt', 'w') as handle:
    for request in (0x40086602, 0x5412, 0x5452):
        try:
            fcntl.ioctl(handle.fileno(), request, struct.pack('i', 0))
        except OSError as error:
            assert error.errno == errno.EPERM
        else:
            raise AssertionError('metadata_or_device_ioctl_allowed')
os.close(reader)
os.close(writer)
print('descriptor_only_ioctls')
""")
    assert result.success, result.stderr
    assert result.stdout == "descriptor_only_ioctls\n"


def test_symlink_and_descendant_process_cannot_escape(isolated):
    workspace, secret = isolated
    (workspace / "escape").symlink_to(secret)
    result = execute(isolated, """
import subprocess, sys
child = subprocess.run([sys.executable, '-c', "open('escape').read()"], capture_output=True, text=True, timeout=3)
print(child.returncode)
assert child.returncode != 0 and 'PermissionError' in child.stderr
""")
    assert result.success, result.stderr


def test_tool_cannot_inspect_or_signal_unsandboxed_parent(isolated):
    result = execute(isolated, """
import os
for operation in [lambda: open('/proc/%s/environ' % os.getppid()).read(),
                  lambda: os.kill(os.getppid(), 0)]:
    try:
        operation()
    except PermissionError:
        pass
    else:
        raise AssertionError('parent_not_isolated')
print('parent_isolated')
""")
    assert result.success, result.stderr


def test_caller_environment_cannot_disable_launch_or_inject_loader(isolated):
    result = execute(isolated, """
import os
assert not any(key in os.environ for key in ['LD_PRELOAD', 'PYTHONPATH', 'AGENT_TOKEN',
                                             'ANANTA_NATIVE_RESULT_STORE_PATH'])
print('clean_environment')
""", environment={"ANANTA_NATIVE_RESULT_STORE_PATH": "", "LD_PRELOAD": "/tmp/attack.so",
                   "PYTHONPATH": "/tmp/attack", "AGENT_TOKEN": "synthetic-private-token"})
    assert result.success, result.stderr


def test_tools_cannot_change_store_permissions_timestamps_or_metadata(isolated):
    _workspace, secret = isolated
    before = secret.stat()
    result = execute(isolated, f"""
import os
for operation in [lambda: os.chmod({str(secret)!r}, 0o777),
                  lambda: os.chown({str(secret)!r}, os.getuid(), os.getgid()),
                  lambda: os.utime({str(secret)!r}, (1, 1)),
                  lambda: os.setxattr({str(secret)!r}, 'user.escape', b'unsafe')]:
    try:
        operation()
    except PermissionError:
        pass
    else:
        raise AssertionError('metadata_escape')
print('metadata_isolated')
""")
    assert result.success, result.stderr
    after = secret.stat()
    assert (after.st_mode, after.st_uid, after.st_gid, after.st_mtime_ns) == (
        before.st_mode, before.st_uid, before.st_gid, before.st_mtime_ns)


def test_tools_cannot_create_io_uring_metadata_bypass(isolated):
    result = execute(isolated, """
import ctypes, errno
libc=ctypes.CDLL(None, use_errno=True)
assert libc.syscall(425, 1, 0) == -1 and ctypes.get_errno() == errno.EPERM
print('async_metadata_fenced')
""")
    assert result.success, result.stderr


def test_private_store_overlap_denied_before_subprocess(isolated, monkeypatch):
    _workspace, secret = isolated
    monkeypatch.setattr("worker.shell.command_executor.subprocess.run", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(PermissionError, match="binding_denied"):
        execute((secret.parent, secret), "print('unsafe')")


def test_application_store_binding_is_authoritative(isolated, monkeypatch):
    workspace, secret = isolated
    monkeypatch.delenv("ANANTA_NATIVE_RESULT_STORE_PATH")
    app = Flask(__name__)
    app.config["ANANTA_NATIVE_RESULT_STORE_PATH"] = str(secret)
    with app.app_context():
        with isolated_command(["echo", "safe"], workspace=workspace,
                              environment={"PATH": "/usr/bin"}) as (argv, environment, descriptors):
            assert "filesystem_launcher.py" in argv[2] and str(secret) not in environment.values()
            assert len(descriptors) == 1


def test_active_store_cannot_be_disabled_by_clearing_configuration(isolated, monkeypatch):
    from types import SimpleNamespace

    _workspace, secret = isolated
    monkeypatch.delenv("ANANTA_NATIVE_RESULT_STORE_PATH")
    app = Flask(__name__)
    app.extensions["native_result_store"] = SimpleNamespace(path=secret)
    with app.app_context():
        result = execute(isolated, f"""
from pathlib import Path
try:
    Path({str(secret)!r}).read_text()
except PermissionError:
    print('active_store_still_isolated')
else:
    raise AssertionError('configuration_drift_escape')
""")
    assert result.success, result.stderr


def test_older_or_unavailable_landlock_never_continues(monkeypatch, tmp_path):
    kernel = LandlockKernel()
    calls = []
    monkeypatch.setattr(kernel, "_call", lambda *args: calls.append(args) or 5)
    with pytest.raises(PermissionError, match="abi_required"):
        kernel.restrict(readonly=["/usr"], workspace=str(tmp_path))
    assert calls == [(444, 0, 0, 1)]


def test_unfixed_disconnected_directory_kernel_never_installs_rules(monkeypatch, tmp_path):
    kernel = LandlockKernel()
    results = iter([7, 0])
    calls = []
    monkeypatch.setattr(kernel, "_call", lambda *args: calls.append(args) or next(results))
    with pytest.raises(PermissionError, match="erratum_required"):
        kernel.restrict(readonly=["/usr"], workspace=str(tmp_path))
    assert calls == [(444, 0, 0, 1), (444, 0, 0, 2)]


@pytest.mark.parametrize("stage", ["landlock", "seccomp"])
def test_launcher_failure_never_runs_the_command(monkeypatch, capsys, stage):
    from types import SimpleNamespace

    from worker.shell import filesystem_launcher

    def fail():
        raise PermissionError("synthetic-unavailable")

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", ["launcher", json.dumps({"readonly": ["/usr"], "workspace": "/tmp",
                                                            "workspace_fd": 123}), "must-not-run"])
    monkeypatch.setitem(sys.modules, "landlock_kernel", SimpleNamespace(
        LandlockKernel=lambda: SimpleNamespace(restrict=lambda **kwargs: fail() if stage == "landlock" else None)))
    monkeypatch.setitem(sys.modules, "metadata_seccomp", SimpleNamespace(install_metadata_guard=fail))
    monkeypatch.setattr(filesystem_launcher.os, "close", lambda fd: None)
    monkeypatch.setattr(filesystem_launcher.os, "execvpe", lambda *args: pytest.fail("must not run command"))
    assert filesystem_launcher.main() == 126
    assert capsys.readouterr().err == "native_tool_filesystem_isolation_denied\n"


def test_workspace_descriptor_remains_bound_across_path_replacement(isolated):
    import subprocess

    workspace, secret = isolated
    code = f"""
from pathlib import Path
Path('pinned.txt').write_text('allowed')
try:
    Path({str(secret)!r}).read_text()
except PermissionError:
    print('private_denied')
else:
    raise AssertionError('path_rebinding_escape')
"""
    with isolated_command([sys.executable, "-c", code], workspace=workspace,
                          environment={"PATH": "/usr/bin"}) as (argv, environment, descriptors):
        moved = workspace.with_name("original-workspace")
        workspace.rename(moved)
        workspace.symlink_to(secret.parent)
        outcome = subprocess.run(argv, cwd=moved, env=environment, pass_fds=descriptors,
                                 capture_output=True, text=True, timeout=10, check=False)
    assert outcome.returncode == 0, outcome.stderr
    assert (moved / "pinned.txt").read_text() == "allowed" and not (secret.parent / "pinned.txt").exists()
