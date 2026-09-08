"""Sandboxed private browser and TLS listeners in one network-less container."""

import json
import os
import re
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from tests.meet_browser_network_server import certificate


@pytest.mark.skipif(os.environ.get("MEET_BROWSER_NETWORK_GATE") != "1", reason="explicit private Chromium network gate")
@pytest.mark.timeout(90)
def test_actual_browser_blocks_foreign_destinations_and_redirects_before_contact(tmp_path):
    image = os.environ.get("MEET_BROWSER_NETWORK_IMAGE", "")
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image), "immutable local runtime image required"
    root = Path(__file__).resolve().parents[1]
    identity = uuid4().hex
    name = "meet-test-network-boundary-" + identity[:12]
    pem, private, spki = certificate(tmp_path)
    files = (
        "worker/meet_media/browser_network.py",
        "tests/meet_browser_network_scenario.py",
        "tests/meet_browser_network_server.py",
    )

    def docker(*args, timeout=15):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)

    try:
        result = docker(
            "create",
            "--name",
            name,
            "--label",
            "ananta.test-run=" + identity,
            "--network=none",
            "--user=1000:1000",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--security-opt=seccomp=" + str(root / "docker/meet-media/chromium-seccomp.json"),
            "--cpus=2",
            "--memory=768m",
            "--pids-limit=256",
            "--shm-size=128m",
            "--tmpfs=/tmp:size=128m,mode=1777",
            "--env=PYTHONPATH=/app",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            "--mount",
            f"type=bind,src={pem},dst=/test/ca.pem,readonly",
            "--mount",
            f"type=bind,src={private},dst=/test/key.pem,readonly",
            *(
                argument
                for file in files
                for argument in ("--mount", f"type=bind,src={root / file},dst=/app/{file},readonly")
            ),
            "--entrypoint=timeout",
            image,
            "45s",
            "python",
            "-c",
            "import json,sys,faulthandler; faulthandler.dump_traceback_later(35); "
            "from tests.meet_browser_network_scenario import run; "
            "print(json.dumps(run('/test/ca.pem', '/test/key.pem', sys.argv[1])))",
            spki,
            timeout=25,
        )
        assert result.returncode == 0, "private network fixture create failed"
        result = docker("start", "--attach", name, timeout=50)
        assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
        lines = result.stdout.splitlines()
        assert lines[:-1] == [
            "fixture-phase:" + phase
            for phase in (
                "browser",
                "page",
                "socket",
                "foreign-socket",
                "socket-denials",
                "http-denials",
                "worker-denials",
            )
        ]
        assert json.loads(lines[-1]) == {"foreign_requests": 0, "http_asset": True, "websocket_greeting": True}
    finally:
        owner = docker("inspect", "-f", '{{index .Config.Labels "ananta.test-run"}}', name)
        assert owner.returncode == 0 and owner.stdout.strip() == identity, "owned fixture cleanup unconfirmed"
        assert docker("rm", "--force", name).returncode == 0, "owned fixture cleanup failed"
