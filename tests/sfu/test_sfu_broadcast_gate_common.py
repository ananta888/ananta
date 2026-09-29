from __future__ import annotations

import os
import signal
import sys

from scripts import sfu_broadcast_gate_common as gate_common

MIB = 1024 * 1024
GIB = 1024 * MIB


def test_address_space_guard_is_finite_and_separate_from_rss_budget() -> None:
    assert gate_common._address_space_limit_bytes(GIB) == 64 * GIB
    assert gate_common._address_space_limit_bytes(3 * GIB) == 64 * GIB
    assert gate_common._address_space_limit_bytes(20 * GIB) == 80 * GIB


def test_sparse_virtual_mapping_does_not_consume_the_rss_budget(tmp_path) -> None:
    result = gate_common.run_bounded_command(
        [
            sys.executable,
            "-c",
            "import mmap; region = mmap.mmap(-1, 512 * 1024 * 1024); assert len(region) > 0",
        ],
        cwd=tmp_path,
        env=os.environ,
        timeout_seconds=5,
        cpu_seconds_max=5,
        memory_bytes_max=64 * MIB,
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.peak_rss_bytes <= 64 * MIB


def test_aggregate_process_group_rss_is_enforced(tmp_path) -> None:
    worker = "import time; payload = bytearray(40 * 1024 * 1024); time.sleep(10)"
    parent = (
        "import subprocess, sys, time; "
        f"worker = {worker!r}; "
        "children = [subprocess.Popen([sys.executable, '-c', worker]) for _ in range(2)]; "
        "time.sleep(10)"
    )
    result = gate_common.run_bounded_command(
        [sys.executable, "-c", parent],
        cwd=tmp_path,
        env=os.environ,
        timeout_seconds=5,
        cpu_seconds_max=5,
        memory_bytes_max=64 * MIB,
    )

    assert result.exit_code == -signal.SIGKILL
    assert result.timed_out is False
    assert result.peak_rss_bytes > 64 * MIB
    assert result.elapsed_ms < 5_000
