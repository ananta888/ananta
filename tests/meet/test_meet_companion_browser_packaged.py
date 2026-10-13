"""Pinned browser setup under the existing production sandbox profile, not full run()."""

import json
import os
import re
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.timeout(60)
@pytest.mark.skipif(
    os.environ.get("MEET_COMPANION_PACKAGED_GATE") != "1", reason="explicit private packaged browser gate"
)
def test_packaged_companion_browser_starts_with_existing_chromium_sandbox_profile():
    image = os.environ.get("MEET_COMPANION_PACKAGED_IMAGE", "")
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image)
    root = Path(__file__).resolve().parents[2]
    profile = root / "docker/meet-media/chromium-seccomp.json"
    token = uuid4().hex
    name = "ananta-companion-browser-" + token[:12]
    script = """
import json
import os
from playwright.sync_api import sync_playwright
assert os.geteuid() != 0
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
        args=["--autoplay-policy=no-user-gesture-required"], timeout=15000)
    try:
        context = browser.new_context(permissions=[], accept_downloads=False, service_workers="block")
        page = context.new_page()
        page.set_content("<title>synthetic</title>")
        assert page.title() == "synthetic"
    finally:
        browser.close()
print(json.dumps({"sandbox": True, "browser_page": True, "nonroot": True}))
"""
    command = ["docker", "run", "--rm", "-i", "--init", "--name", name,
               "--label", "ananta.test-owner=" + token, "--network", "none", "--read-only",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
               "--security-opt", "seccomp=" + str(profile), "--user", "11111:11111",
               "--pids-limit", "256", "--memory", "2g", "--cpus", "2", "--shm-size", "256m",
               "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777", "--entrypoint", "python", image, "-"]
    try:
        result = subprocess.run(command, input=script, capture_output=True, text=True, timeout=35)
        assert result.returncode == 0, "private sandboxed browser setup failed; raw browser diagnostic omitted"
        assert json.loads(result.stdout) == {"sandbox": True, "browser_page": True, "nonroot": True}
    finally:
        owner = subprocess.run(["docker", "inspect", "-f", '{{index .Config.Labels "ananta.test-owner"}}', name],
                               capture_output=True, text=True, timeout=5)
        if owner.returncode == 0 and owner.stdout.strip() == token:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=True, timeout=10)
