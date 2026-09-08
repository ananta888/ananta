"""Bounded network-less browser probe; mounts only named current-source files."""

import json
import re
import subprocess
from pathlib import Path
from uuid import uuid4


def run_private_browser_probe(image, module, files):
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image), "immutable private browser image required"
    assert re.fullmatch(r"tests\.[a-z_]+", module), "closed probe module required"
    root = Path(__file__).resolve().parents[1]
    assert 1 <= len(files) <= 12 and len(set(files)) == len(files)
    for relative in files:
        assert re.fullmatch(r"(?:tests|worker/meet_media|ananta_contracts)/[a-z_]+\.py", relative)
        file = root / relative
        assert file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(root)
    identity = uuid4().hex
    name = "meet-test-private-view-" + identity

    def docker(*arguments, timeout=15):
        return subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=timeout)

    created = False
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
            *(
                argument
                for relative in files
                for argument in ("--mount", f"type=bind,src={root / relative},dst=/app/{relative},readonly")
            ),
            "--entrypoint=timeout",
            image,
            "45s",
            "python",
            "-m",
            module,
            timeout=25,
        )
        assert result.returncode == 0, "private browser probe creation failed"
        created = True
        result = docker("start", "--attach", name, timeout=50)
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
        assert len(result.stdout) <= 4096, "private probe output budget exceeded"
        return json.loads(result.stdout)
    finally:
        owner = docker("inspect", "-f", '{{index .Config.Labels "ananta.test-run"}}', name)
        if owner.returncode == 0:
            assert owner.stdout.strip() == identity, "private probe ownership changed"
            assert docker("rm", "--force", name).returncode == 0, "private probe cleanup failed"
        else:
            assert not created, "created private probe cleanup unconfirmed"
