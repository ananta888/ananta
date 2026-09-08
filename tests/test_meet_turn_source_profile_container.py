"""Installed classification before execution; not GPU/media/production evidence."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.mark.timeout(70)
@pytest.mark.skipif(os.environ.get("MEET_TURN_PROFILE_GATE") != "1", reason="explicit installed Worker profile gate")
def test_installed_renderer_classifies_sources_and_rejects_capture_before_execution():
    image = os.environ.get("MEET_TURN_PROFILE_IMAGE", "")
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image), "immutable locally installed Worker image required"
    root = Path(__file__).resolve().parents[1]
    paths = ("ananta_contracts/meet_turn_source_profile.py", "worker/meet_media/local_runtime.py")
    digests = [hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths]
    identity = uuid4().hex
    name = "ananta-meet-profile-" + identity[:12]
    script = """
import hashlib, importlib.util, json, os, sys
from pathlib import Path
from unittest.mock import patch
from ananta_contracts.meet_turn_source_profile import turn_source_profile
from worker.meet_media import local_runtime
assert os.geteuid() != 0 and importlib.util.find_spec('agent') is None
paths = ('ananta_contracts/meet_turn_source_profile.py', 'worker/meet_media/local_runtime.py')
for path, digest in zip(paths, sys.argv[1:]):
    assert hashlib.sha256(Path('/app', path).read_bytes()).hexdigest() == digest
variants = 0
for image, video in ((False, False), (True, False), (False, True)):
    for publish in (False, True):
        value = turn_source_profile(persona_image=image, persona_video=video, publish=publish).projection()
        assert bool(value['publication_sources']) == publish
        assert 'human_device_capture' in value['denied_operations']
        assert value['input_sources'] == (['persona_image'] if image else ['persona_video'] if video else [])
        variants += 1
with patch.object(local_runtime.tempfile, 'TemporaryDirectory', side_effect=AssertionError('no execution')) as execute:
    for field in ('source_profile', 'source_class', 'execution_profile', 'human_device_capture'):
        try:
            local_runtime.run({field: 'forbidden'})
        except ValueError as error:
            assert str(error) == 'meet_turn_source_profile_invalid'
        else:
            raise AssertionError('capture profile accepted')
    execute.assert_not_called()
print(json.dumps({'variants': variants, 'pre_execution_denials': 4, 'gpu': False, 'production_evidence': False}))
"""

    def docker(*args, timeout=10):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)

    phase = "create"
    created = False
    try:
        creation = docker(
            "create",
            "--name",
            name,
            "--label",
            "ananta.test-run=" + identity,
            "--network",
            "none",
            "--user",
            f"{os.geteuid()}:{os.getegid()}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--cpus",
            "0.5",
            "--memory",
            "256m",
            "--pids-limit",
            "32",
            "--tmpfs",
            "/tmp:rw,size=16m,mode=1777",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            image,
            "timeout",
            "15s",
            "python",
            "-c",
            script,
            *digests,
            timeout=25,
        )
        assert creation.returncode == 0, "installed profile fixture creation failed; runtime details redacted"
        created = True
        phase = "execute"
        result = docker("start", "--attach", name, timeout=25)
        assert result.returncode == 0, "bounded installed profile check failed; runtime details redacted"
        assert result.stderr == ""
        assert json.loads(result.stdout) == {
            "variants": 6,
            "pre_execution_denials": 4,
            "gpu": False,
            "production_evidence": False,
        }
    except subprocess.TimeoutExpired:
        # A timed-out Docker create can complete asynchronously in the daemon.
        # Do not print a full command/script or claim the absent object is clean.
        raise AssertionError("installed profile fixture timed out during " + phase) from None
    finally:
        owner = docker("inspect", "-f", '{{index .Config.Labels "ananta.test-run"}}', name)
        if owner.returncode == 0 and owner.stdout.strip() == identity:
            cleaned = docker("rm", "--force", name)
            assert cleaned.returncode == 0, "owned profile fixture cleanup failed"
        elif not created:
            raise AssertionError("profile fixture creation/cleanup unconfirmed; inspect the exact test-owned label")
