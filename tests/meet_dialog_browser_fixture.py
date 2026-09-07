"""Test-owned sandboxed browser; no public ports, GPU, host profiles or mounts."""

import ipaddress
import json
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=30).stdout.strip()


class DialogBrowserFixture:
    """Infrastructure-only launch seam; never substitutes Hub or Meet authority."""

    def __init__(self, network, lifetime, *, command=docker):
        if not isinstance(network, str) or not re.fullmatch(r"meet-test-tls-[a-f0-9-]{36}-network", network):
            raise ValueError("test_browser_network_invalid")
        if type(lifetime) is not int or not 180 <= lifetime <= 7380:
            raise ValueError("test_browser_lifetime_invalid")
        self.network, self.lifetime, self.command = network, lifetime, command
        self.name = "meet-test-browser-" + str(uuid4())
        self.created = False
        self.endpoint = None
        self.process_id = None

    def start(self, spki):
        if self.created or not isinstance(spki, str) or not re.fullmatch(r"[A-Za-z0-9+/]{43}=", spki):
            raise ValueError("test_browser_start_invalid")
        info = json.loads(self.command("network", "inspect", self.network))[0]
        if info.get("Internal") is not True:
            raise ValueError("test_browser_network_invalid")
        image = self.command(
            "image",
            "inspect",
            os.environ.get("MEET_TEST_BROWSER_IMAGE", "ananta-meet-media-meet-media-worker:latest"),
            "--format",
            "{{.Id}}",
        )
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            raise ValueError("test_browser_image_invalid")
        seccomp = Path(__file__).resolve().parents[1] / "docker/meet-media/chromium-seccomp.json"
        package = "/usr/local/lib/python3.12/site-packages/playwright/driver/package"
        script = (
            f"const p=require({json.dumps(package)});"
            f"setTimeout(()=>process.exit(0),{self.lifetime * 1000});"
            "p.chromium.launchServer({headless:true,chromiumSandbox:true,host:'0.0.0.0',port:8099,"
            "timeout:15000,args:['--autoplay-policy=no-user-gesture-required',"
            f"'--ignore-certificate-errors-spki-list={spki}']}})"
            ".then(s=>console.log(s.wsEndpoint())).catch(()=>{console.log('test_browser_launch_failed');process.exit(1)})"
        )
        self.created = True  # Cleanup also covers an uncertain Docker create result.
        self.command(
            "create",
            "--name",
            self.name,
            "--network",
            self.network,
            "--user=1000:1000",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--security-opt=seccomp=" + str(seccomp),
            "--tmpfs=/tmp:size=256m,mode=1777",
            "--shm-size=256m",
            "--memory=1g",
            "--pids-limit=256",
            "--cpus=2",
            "--init",
            "--entrypoint=" + package.removesuffix("/package") + "/node",
            image,
            "-e",
            script,
        )
        self.command("start", self.name)
        self.process_id = int(self.command("inspect", self.name, "--format", "{{.State.Pid}}"))
        address = self.command(
            "inspect",
            self.name,
            "--format",
            "{{(index .NetworkSettings.Networks " + json.dumps(self.network) + ").IPAddress}}",
        )
        if not ipaddress.IPv4Address(address).is_private:
            raise ValueError("test_browser_address_invalid")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            lines = self.command("logs", self.name).splitlines()
            for line in lines:
                match = re.fullmatch(r"ws://(?:localhost|0\.0\.0\.0):8099/([a-f0-9]{32})", line)
                if match:
                    self.endpoint = f"ws://{address}:8099/{match[1]}"
                    return
                if line == "test_browser_launch_failed":
                    raise ValueError("test_browser_sandbox_launch_failed")
            time.sleep(0.1)
        raise ValueError("test_browser_start_timeout")

    def launch(self, browser_type, *args, **kwargs):
        if (
            args
            or kwargs
            != {"headless": True, "chromium_sandbox": True, "args": ["--autoplay-policy=no-user-gesture-required"]}
            or self.endpoint is None
        ):
            raise ValueError("test_browser_launch_contract_changed")
        # Full Playwright transport preserves CDP sessions; no network tunnelling
        # back into the host and no connection to any pre-existing user browser.
        return browser_type.connect(self.endpoint, timeout=15000)

    def close(self):
        if self.created:
            self.created = False
            self.command("rm", "--force", self.name)
