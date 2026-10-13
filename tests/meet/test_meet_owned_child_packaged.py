"""Actual constrained image proves the parent-handle seccomp rule stays narrow."""

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from tests.meet_dialog_browser_fixture import docker


@pytest.mark.skipif(os.environ.get("MEET_COMPANION_PACKAGED_GATE") != "1", reason="explicit owned helper image gate")
@pytest.mark.timeout(90)
def test_packaged_helper_parent_guard_with_existing_sandbox_and_narrow_pidfd_rule():
    root = Path(__file__).resolve().parents[2]
    name = "meet-test-owned-parent-" + uuid4().hex
    code = '''
import errno,json,os,signal,sys
from worker.meet_media.companion_process_transport import CompanionProcessTransport
fd=os.pidfd_open(os.getpid(),0)
try:
 for operation in (lambda:os.pidfd_open(os.getpid(),os.O_NONBLOCK),lambda:signal.pidfd_send_signal(fd,0)):
  try: operation()
  except OSError as error: assert error.errno == errno.EPERM
  else: raise AssertionError("unnecessary process-handle operation allowed")
finally: os.close(fd)
command="import sys,json;sys.stdin.readline();print(json.dumps({'ready':True}),flush=True)"
t=CompanionProcessTransport((sys.executable,"-u","-c",command))
try: assert t.exchange({},cooperate=lambda:None)=={'ready':True}
finally: t.close()
print(json.dumps({'owned_helper':True,'nonzero_flags_denied':True,'pidfd_signal_denied':True,'production_release_evidence':False}))
'''
    try:
        docker("create", "--name", name, "--pull=never", "--network=none", "--read-only", "--user=1000:1000",
               "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=128m", "--pids-limit=32",
               "--security-opt=seccomp=" + str(root / "docker/meet-media/chromium-seccomp.json"),
               "--entrypoint=python", os.environ["MEET_COMPANION_PACKAGED_IMAGE"], "-c", code)
        result = json.loads(docker("start", "--attach", name))
        assert result == {"owned_helper": True, "nonzero_flags_denied": True, "pidfd_signal_denied": True,
                          "production_release_evidence": False}
    finally:
        docker("rm", "--force", name)
