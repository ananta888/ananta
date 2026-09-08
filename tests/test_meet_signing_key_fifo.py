"""Bound the historical FIFO startup hang in an owned disposable process."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.timeout(15)
def test_signing_key_fifo_is_rejected_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "synthetic-private-key.fifo"
    os.mkfifo(path, 0o600)
    script = """
import sys
from agent.services.meet_machine_grant import MeetMachineGrantIssuer
try:
    MeetMachineGrantIssuer('https://synthetic-hub.test', sys.argv[1])
except ValueError as error:
    assert str(error) == 'meet_machine_key_file_invalid'
else:
    raise AssertionError('FIFO accepted as signing key')
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(path)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            timeout=2,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("meet_signing_key_fifo_open_or_read_waited_for_a_writer", pytrace=False)
    assert result.returncode == 0, "bounded FIFO rejection missing"
