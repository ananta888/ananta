"""Exact standalone image, private nonroot container, no mounted repo or Hub."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

SOURCE_MODULES = (
    "companion.py", "companion_supervisor.py", "companion_media.py", "companion_chat_inbox.py",
    "companion_chat_pump.py", "companion_browser_progress.py",
    "companion_chat_diagnostics.py", "companion_chat_inbox_processing.py",
    "companion_chat_ledger.py",
    "companion_task_grant_client.py",
    "companion_task_tool_client.py",
    "companion_task_model_client.py",
    "companion_task_model_process.py", "companion_task_model_runtime.py",
    "companion_assigned_provider.py",
    "companion_assigned_tools.py", "llm_tools.py",
    "companion_assigned_dialog.py", "companion_dialog.py", "companion_router.py",
    "companion_task_model_selection.py",
    "companion_task_cuda_cache.py",
    "companion_task_scratch.py",
    "owned_child_runtime.py",
    "companion_explanation.py", "llm.py", "llm_backends.py",
    "companion_task_tool_process.py", "companion_task_tool_runtime.py", "companion_execution_ports.py",
    "companion_task_controls.py",
    "companion_task_execution.py", "companion_task_runtime.py",
    "companion_task_speech.py", "companion_native_sequence.py", "companion_sequence_speech.py",
    "companion_task_environment.py",
    "companion_execution_process.py", "companion_execution_runtime.py", "companion_process_transport.py",
    "companion_media_preparation.py", "companion_media_process.py", "companion_media_runtime.py",
    "dialog_avatar_pump.py",
    "dialog_executor.py", "dialog_progress_channel.py", "dialog_progress_budget.py",
    "dialog_progress_watch.py", "dialog_slots.py",
    "dialog_client.py", "dialog_control_exchange.py", "dialog_membership_loss.py",
)


@pytest.mark.timeout(90)
@pytest.mark.skipif(os.environ.get("MEET_COMPANION_PACKAGED_GATE") != "1", reason="explicit private packaged CPU gate")
def test_packaged_companion_helpers_match_source_and_run_without_hub_or_writable_root():
    image = os.environ.get("MEET_COMPANION_PACKAGED_IMAGE", "")
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image), "explicit immutable local Worker image required"
    root = Path(__file__).resolve().parents[2]
    digests = {"worker/meet_media/" + name: hashlib.sha256((root / "worker/meet_media" / name).read_bytes()).hexdigest()
               for name in SOURCE_MODULES}
    for contract_path in ("ananta_contracts/meet_avatar_source.py", "ananta_contracts/meet_companion_task_grant.py",
                          "ananta_contracts/meet_dialog.py", "ananta_contracts/meet_companion_profile.py",
                          "ananta_contracts/meet_companion_task_tool.py", "ananta_contracts/hub_evidence.py",
                          "ananta_contracts/meet_companion_model_input.py",
                          "ananta_contracts/meet_companion_task_model.py"):
        digests[contract_path] = hashlib.sha256((root / contract_path).read_bytes()).hexdigest()
    token = uuid4().hex
    name = "ananta-meet-companion-probe-" + token[:12]

    def docker(*arguments, input=None, timeout=10, check=True):
        result = subprocess.run(["docker", *arguments], input=input, capture_output=True, text=True, timeout=timeout)
        if check:
            assert result.returncode == 0, "private packaged probe failed; diagnostic payload redacted"
        return result

    command = ["run", "--rm", "-i", "--init", "--name", name, "--label", "ananta.test-owner=" + token,
               "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
               "--user", "11111:11111", "--pids-limit", "64", "--memory", "1g", "--cpus", "2",
               "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
               "--tmpfs", "/state:rw,nosuid,nodev,size=32m,mode=0700,uid=11111,gid=11111",
               "-e", "ANANTA_CPU_FALLBACK=1", "-e", "MEET_AVATAR_SERVICE_ENABLED=0",
               "-e", "MEET_COMPANION_LOG=/state/synthetic-companion.log",
               "-e", "MEET_AVATAR_IDLE_CLIP=/state/missing-idle.mp4",
               "-e", "ANANTA_TEST_SOURCE_DIGESTS=" + json.dumps(digests), "--entrypoint", "python", image, "-"]
    try:
        script = Path(__file__).with_name("meet_companion_packaged_probe.py").read_text()
        result = docker(*command, input=script, timeout=75)
        receipt = json.loads(result.stdout)
        assert receipt == {"schema": "ananta.meet-companion-packaged-probe.v1", "nonroot": True,
                           "hub_package_absent": True, "exact_source_modules": len(digests),
                           "runtime_lock": "passed", "persistent_model_pipe": "passed",
                           "default_model_entry_denial": "passed",
                           "execution_ports": "passed",
                           "real_cpu_media_entry": "passed", "media_cancellation": "passed"}
    finally:
        owner = docker("inspect", "-f", '{{index .Config.Labels "ananta.test-owner"}}', name, check=False)
        if owner.returncode == 0 and owner.stdout.strip() == token:
            docker("rm", "-f", name)
