"""Optional, fully headless smoke of the pinned sidecar in an offline container."""

import json
import os
import subprocess
import time
import uuid

import pytest

from plugins.evolver_adapter.adapter import EvolverAdapter

_IMAGE = os.environ.get("ANANTA_EVOLVER_PINNED_IMAGE")


@pytest.mark.integration
@pytest.mark.skipif(not _IMAGE, reason="explicit locally built pinned runtime image required")
def test_pinned_runtime_health_as_nonroot_in_readonly_offline_container():
    name = "ananta-evolver-runtime-test-" + uuid.uuid4().hex
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--memory",
                "256m",
                "--cpus",
                "1",
                "--pids-limit",
                "32",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=64m,uid=1000,gid=1000",
                "--env",
                "EVOLVER_TIMEOUT_MS=1000",
                _IMAGE,
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
        probe = (
            "const r=await fetch('http://127.0.0.1:8080/health');"
            "console.log(JSON.stringify({code:r.status,body:await r.json()}));"
        )
        deadline = time.monotonic() + 15
        response = None
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["docker", "exec", name, "node", "--input-type=module", "-e", probe],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                response = json.loads(result.stdout)
                break
            time.sleep(0.1)
        assert response is not None, "runtime did not become healthy within bounded deadline"
        assert response["code"] == 200
        metadata = response["body"]["runtime"]
        assert metadata["version"] == "1.94.0"
        assert metadata["commit"] == "b34ccbcd9a07dc252b080b80e0c587734554f004"
        assert metadata["analyze_only"] is True

        class Transport:
            def health(self):
                return response["body"]

        descriptor = EvolverAdapter(transport=Transport()).describe()
        assert descriptor.status == "available" and descriptor.provider_metadata["runtime"] == metadata
        assert subprocess.check_output(["docker", "exec", name, "id", "-u"], text=True, timeout=5).strip() == "1000"
        # Real pinned CLI invocation is bounded even without a model gateway.
        # Success is not required here: this is a containment/cleanup probe,
        # not a fabricated successful production GEP evaluation.
        analyze = (
            "const r=await fetch('http://127.0.0.1:8080/evolution/analyze',{method:'POST',"
            "headers:{'Content-Type':'application/json'},body:JSON.stringify({context:"
            "{objective:'synthetic containment fixture',constraints:{synthetic:true}}})});"
            "console.log(JSON.stringify({code:r.status,body:await r.json()}));"
        )
        executed = subprocess.run(
            ["docker", "exec", name, "node", "--input-type=module", "-e", analyze],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        outcome = json.loads(executed.stdout)
        assert outcome["code"] in {200, 500}
        remaining = subprocess.check_output(
            [
                "docker",
                "exec",
                name,
                "node",
                "-e",
                "console.log(require('fs').readdirSync('/tmp').filter(n=>n.startsWith('evolver-run-')).length)",
            ],
            text=True,
            timeout=5,
        )
        assert remaining.strip() == "0"
    finally:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=10, check=False)
