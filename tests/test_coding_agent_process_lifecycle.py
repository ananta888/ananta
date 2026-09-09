"""Bounded real-process regressions; no model, network, browser or human input."""

import os
import signal
import sys
import time
from pathlib import Path
from threading import Event

import pytest

from agent.cli_backends.coding_agent_process import BoundedCodingAgentProcess


def _run(workspace, script, **options):
    return BoundedCodingAgentProcess().run(
        (sys.executable, "-c", script),
        cwd=workspace,
        environment={"PATH": "/usr/bin:/bin"},
        cancellation=Event(),
        maximum_output_chars=1024,
        **options,
    )


@pytest.mark.timeout(15)
def test_blocked_prompt_input_does_not_delay_the_wall_clock_limit(tmp_path):
    result = _run(
        tmp_path,
        "import time; time.sleep(1.5)",
        timeout_seconds=0.15,
        input_text="x" * 200_000,
    )
    assert result.reason_code == "timeout"
    assert result.duration_ms < 1100, "stdin must not block deadline supervision"


@pytest.mark.timeout(15)
def test_unterminated_output_hits_limit_before_process_or_line_ends(tmp_path):
    result = _run(
        tmp_path,
        "import sys,time; sys.stdout.write('x' * 65536); sys.stdout.flush(); time.sleep(1.5)",
        timeout_seconds=3,
    )
    assert result.reason_code == "output_limit_exceeded"
    assert result.return_code == 65
    assert result.duration_ms < 1100, "output accounting must not wait for newline or EOF"
    assert len(result.stdout) <= 1024


@pytest.mark.timeout(15)
def test_large_utf8_prompt_is_completely_delivered_without_changing_line_events(tmp_path):
    prompt = "Grüße 🙂\n" * 20_000
    result = _run(
        tmp_path,
        "import sys; value=sys.stdin.read(); print(len(value)); print('done')",
        timeout_seconds=3,
        input_text=prompt,
    )
    assert result.reason_code == "completed"
    assert result.stdout == f"{len(prompt)}\ndone\n"


@pytest.mark.timeout(15)
@pytest.mark.parametrize("failed_start", [1, 2, 3])
def test_partial_pipe_thread_start_failure_still_reaps_owned_process(tmp_path, monkeypatch, failed_start):
    import threading

    from agent.cli_backends import coding_agent_process as module

    created, starts = [], []
    original_popen, original_start = module.subprocess.Popen, threading.Thread.start

    def popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        created.append(child)
        return child

    def start(thread):
        if getattr(thread._target, "__module__", "") == "agent.cli_backends.coding_agent_process_io":
            starts.append(thread)
            if len(starts) == failed_start:
                raise RuntimeError("synthetic pipe start failure")
        return original_start(thread)

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(RuntimeError, match="synthetic pipe start failure"):
        _run(tmp_path, "import time; time.sleep(2)", timeout_seconds=3, input_text="prompt")
    assert len(created) == 1
    assert created[0].poll() is not None
    assert all(stream.closed for stream in (created[0].stdin, created[0].stdout, created[0].stderr))
    assert not any(thread.is_alive() for thread in starts)


def _executing(pid):
    try:
        # A reparented zombie is no longer executing; external PID 1 owns reaping.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-owned process-group regression")
@pytest.mark.timeout(15)
@pytest.mark.parametrize("leader_waits", [False, True], ids=["leader-exited", "leader-exits-on-term"])
def test_owned_term_ignoring_descendant_stops_even_when_leader_exits(tmp_path, leader_waits):
    # The handshake proves TERM is ignored before the parent exits. All child
    # standard descriptors are detached; neither pipe EOF nor leader exit proves
    # group completion. A three-second self-expiry also bounds broken versions.
    script = """
import os,signal,time
read_fd,write_fd=os.pipe()
pid=os.fork()
if pid == 0:
    os.close(read_fd)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    null=os.open(os.devnull,os.O_RDWR)
    for fd in (0,1,2): os.dup2(null,fd)
    os.close(null)
    os.write(write_fd,b'1'); os.close(write_fd)
    time.sleep(3)
    os._exit(0)
os.close(write_fd)
assert os.read(read_fd,1)==b'1'
os.close(read_fd)
print(pid,flush=True)
"""
    if leader_waits:
        script += "time.sleep(1.5)\n"
    child_pid = None
    try:
        result = _run(tmp_path, script, timeout_seconds=0.3 if leader_waits else 2)
        child_pid = int(result.stdout.strip())
        assert child_pid > 1
        assert result.reason_code == ("timeout" if leader_waits else "completed")
        # KILL delivery need not have been scheduled when killpg() returns.
        deadline = time.monotonic() + 0.5
        while _executing(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _executing(child_pid), "owned descendant survived leader cleanup"
    finally:
        if child_pid is not None and child_pid > 1 and _executing(child_pid):
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
