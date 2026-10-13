"""Private packaged CPU probe; no browser, Hub grants or provider calls."""

import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from worker.meet_media.companion_chat_pump import CompanionSessionEnded
from worker.meet_media.companion_execution_process import CompanionExecutionProcess
from worker.meet_media.companion_media_process import CompanionMediaPreparationProcess
from worker.meet_media.companion_task_controls import CompanionControlLost, CompanionTaskControls
from worker.meet_media.companion_task_environment import assigned_model_environment
from worker.meet_media.companion_task_execution import AssignedCompanionExecution
from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient
from worker.meet_media.companion_task_model_client import AssignedCompanionModelClient
from worker.meet_media.companion_task_model_process import AssignedCompanionModelProcess
from worker.meet_media.companion_task_runtime import AssignedCompanionSession
from worker.meet_media.companion_task_tool_client import AssignedCompanionToolClient
from worker.meet_media.companion_task_tool_process import AssignedCompanionToolProcess
from worker.meet_media.dialog_executor import DialogExecutor


def main():
    assert os.geteuid() != 0 and importlib.util.find_spec("agent") is None
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    assert int(status["CapEff"].strip(), 16) == 0 and status["NoNewPrivs"].strip() == "1"
    mounts = [line.split(" - ", 1) for line in Path("/proc/self/mountinfo").read_text().splitlines()]
    root_mount = next(before.split() for before, _after in mounts if before.split()[4] == "/")
    assert "ro" in root_mount[5].split(",")
    for path in ("/tmp", "/state"):
        assert next(after.split()[0] for before, after in mounts if before.split()[4] == path) == "tmpfs"
    assert {name for _index, name in socket.if_nameindex()} == {"lo"}
    assert not list(Path("/dev").glob("nvidia*"))
    # Import/construct the new transport with no Hub package or network. Actual
    # signed HTTP verification is a separate host-side integration gate.
    assignment = {"schema": "ananta.meet-dialog-assignment.v1", "task_id": "task", "lease_id": "dispatch",
                  "runtime_id": "runtime", "session_id": "session", "tenant_id": "synthetic",
                  "project_id": "synthetic", "deadline": int(time.time()) + 60,
                  "capabilities": ["chat.read", "chat.send"], "audio_mode": "off",
                  "companion_execution": True,
                  "meeting": {"origin": "https://meet.synthetic.test", "room_id": "room-" + "a" * 18,
                              "grant": "synthetic.grant.placeholder"}}
    client = AssignedCompanionGrantClient(assignment,
        "http://127.0.0.1/api/meet/v1/internal/companion/task-grant", b"synthetic-private-worker-key-32bytes")
    assert client.assignment == assignment
    tools = AssignedCompanionToolClient(client)
    assert tools.endpoint.endswith("/internal/companion/task-tool") and tools.deadline == client.deadline
    model_client = AssignedCompanionModelClient(client)
    assert model_client.endpoint.endswith("/internal/companion/task-model") and model_client.deadline == client.deadline
    model_process = AssignedCompanionModelProcess(client)
    assert model_process.endpoint == client.endpoint and model_process.deadline == client.deadline
    tool_process = AssignedCompanionToolProcess(client)
    assert tool_process.endpoint == client.endpoint and tool_process.deadline == client.deadline
    model_environment = assigned_model_environment(assignment, environment={
        'MEET_COMPANION_TOOLS': '1', 'ANANTA_MEET_AVATAR_CODECOMPASS': '1',
        'MEET_COMPANION_RAG_PREFIX': '1', 'MEET_WORKER_KEY_FILE': '/synthetic/key',
    })
    assert model_environment['MEET_COMPANION_TOOLS'] == model_environment['MEET_COMPANION_RAG_PREFIX'] == '0'
    assert 'MEET_WORKER_KEY_FILE' not in model_environment
    assert callable(AssignedCompanionExecution.answer) and callable(AssignedCompanionSession.tick)
    executor = DialogExecutor('/state/synthetic-replay.sqlite', slots=1)
    assert executor.slots.snapshot() == {'capacity': 1, 'active': 0}
    invalid_assignment = subprocess.run(
        [sys.executable, "-m", "worker.meet_media.companion_task_runtime"],
        input=b'{"schema":"foreign","grant":"synthetic-private-value"}\n', capture_output=True, timeout=10,
    )
    assert invalid_assignment.returncode == 1
    assert invalid_assignment.stdout == b"" and invalid_assignment.stderr == b""
    controls = CompanionTaskControls(client)
    try:
        controls.checkpoint("chat")
    except CompanionControlLost:
        pass
    else:
        raise AssertionError("unobserved Hub controls cannot authorize execution")
    finally:
        controls.close()
    expected = json.loads(os.environ["ANANTA_TEST_SOURCE_DIGESTS"])
    for relative, digest in expected.items():
        assert hashlib.sha256((Path("/app") / relative).read_bytes()).hexdigest() == digest
    locked = subprocess.run([sys.executable, "/app/check_meet_runtime_lock.py", "/app/requirements.lock"],
                            capture_output=True, timeout=10)
    assert locked.returncode == 0 and locked.stdout == b"meet-runtime-lock-ok\n"
    default_model = subprocess.run(
        [sys.executable, "-m", "worker.meet_media.companion_execution_runtime"],
        input=b'{"kind":"foreign","text":"synthetic"}\n', capture_output=True, timeout=10)
    assert default_model.returncode == 2 and default_model.stdout == b"" and default_model.stderr == b""
    ticks = []
    media = CompanionMediaPreparationProcess()
    model = CompanionExecutionProcess(command=(sys.executable, "-u", "-c", """
import json,sys,time
count=0
for line in sys.stdin:
    request=json.loads(line);count+=1;time.sleep(0.15)
    print(json.dumps({'kind':request['kind'],'result':str(count)}),flush=True)
"""))
    try:
        assert model.call("answer", "synthetic", cooperate=lambda: ticks.append(True)) == "1"
        assert model.call("answer", "synthetic", cooperate=lambda: ticks.append(True)) == "2"
        idle = media.idle("thinking", cooperate=lambda: ticks.append(True))
        clips = media.speaking(b"\x00\x01" * 4410, cooperate=lambda: ticks.append(True))
        assert idle["frames"] == 24 and len(clips) == 1 and clips[0]["frames"] == 3
        assert len(ticks) >= 4
    finally:
        media.close()
        model.close()
    assert media._transport.process.poll() is not None and model._process.poll() is not None

    port_calls = []
    port_model = CompanionExecutionProcess(command=(sys.executable, "-u", "-c", """
import json,sys
from worker.meet_media.companion_execution_ports import CompanionExecutionPorts
request=json.loads(sys.stdin.buffer.readline())
ports=CompanionExecutionPorts(sys.stdin.buffer,sys.stdout.buffer,allowed={'tool'})
result=ports.call('tool',{'query':request['text']})
print(json.dumps({'kind':'answer','result':result['text']}),flush=True)
"""), ports={"tool": lambda arguments, **kwargs: port_calls.append(arguments) or {"text": "synthetic"}})
    try:
        assert port_model.call("answer", "scoped", cooperate=lambda: ticks.append(True)) == "synthetic"
        assert port_calls == [{"query": "scoped"}]
    finally:
        port_model.close()
    assert port_model._process.poll() is not None

    cancelled = CompanionMediaPreparationProcess(command=(sys.executable, "-u", "-c", "import time;time.sleep(20)"))

    def revoke():
        raise CompanionSessionEnded("synthetic_authority_revoked")

    try:
        cancelled.speaking(b"\x00\x01", cooperate=revoke)
        raise AssertionError("missing cancellation")
    except CompanionSessionEnded:
        assert cancelled._transport.process.poll() is not None
    finally:
        cancelled.close()
    print(json.dumps({"schema": "ananta.meet-companion-packaged-probe.v1", "nonroot": True,
                      "hub_package_absent": True, "exact_source_modules": len(expected),
                      "runtime_lock": "passed", "persistent_model_pipe": "passed",
                      "default_model_entry_denial": "passed",
                      "execution_ports": "passed",
                      "real_cpu_media_entry": "passed", "media_cancellation": "passed"}))


if __name__ == "__main__":
    main()
