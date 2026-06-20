import subprocess
import threading
from unittest.mock import patch

from agent.cli_backends import sgpt


def _configured_limit(limit: int):
    return {"sgpt_routing": {"backend_parallel_limits": {"sgpt": limit}}}


def test_sgpt_permit_released_on_success(monkeypatch):
    monkeypatch.setattr(sgpt, "_get_agent_config", lambda: _configured_limit(1))
    sgpt._BACKEND_SEMAPHORES.clear()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "ok"
        mock_run.return_value.stderr = ""
        rc, _out, _err = sgpt.run_sgpt_command("hello", timeout=1)

    assert rc == 0
    sem = sgpt._get_backend_semaphore("sgpt", 1)
    assert sem.acquire(blocking=False) is True
    sem.release()


def test_sgpt_permit_released_on_timeout(monkeypatch):
    monkeypatch.setattr(sgpt, "_get_agent_config", lambda: _configured_limit(1))
    sgpt._BACKEND_SEMAPHORES.clear()

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["sgpt"], timeout=1)):
        rc, _out, err = sgpt.run_sgpt_command("hello", timeout=1)

    assert rc == -1
    assert err == "Timeout"
    sem = sgpt._get_backend_semaphore("sgpt", 1)
    assert sem.acquire(blocking=False) is True
    sem.release()


def test_sgpt_permit_released_on_exception(monkeypatch):
    monkeypatch.setattr(sgpt, "_get_agent_config", lambda: _configured_limit(1))
    sgpt._BACKEND_SEMAPHORES.clear()

    with patch("subprocess.run", side_effect=RuntimeError("boom")):
        rc, _out, err = sgpt.run_sgpt_command("hello", timeout=1)

    assert rc == -1
    assert "boom" in err
    sem = sgpt._get_backend_semaphore("sgpt", 1)
    assert sem.acquire(blocking=False) is True
    sem.release()
