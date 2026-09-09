"""Keep the pure SDK HTTP-boundary regressions in the normal pytest gate."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_pi_http_transport_node_contract():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the optional Pi SDK transport contract")
    source = Path(__file__).with_name("pi_http_transport.test.mjs")
    result = subprocess.run(
        [node, "--test", str(source)],
        cwd=source.parent,
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-12000:] + result.stderr[-2000:]
