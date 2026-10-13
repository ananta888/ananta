"""Static adversarial shell fixture inside a genuinely delegated Worker Task."""

import json
import os
import shlex
import sys
from pathlib import Path

from flask import current_app

from worker.shell.command_executor import execute_command_plan


def exercise_worker_store_isolation(task_id):
    private = Path(current_app.config["ANANTA_NATIVE_RESULT_STORE_PATH"])
    assert private.is_file() and os.geteuid() == 65534, "private_store_owner_fixture_missing"
    # The same UID can read it before confinement: Unix permissions alone do
    # not explain the subsequent denials in the actual command subprocess.
    with private.open("rb") as stream:
        assert stream.read(16).startswith(b"SQLite format 3"), "private_store_fixture_invalid"
    root = Path("/tmp/native-tool-workspaces")
    root.mkdir(mode=0o700, exist_ok=True)
    workspace = root / task_id
    workspace.mkdir(mode=0o700)
    code = f"""
import json, os
from pathlib import Path
Path('allowed.txt').write_text('workspace_output')
blocked = []
for operation in [lambda: Path({str(private)!r}).read_bytes(),
                  lambda: Path({str(private)!r}).write_bytes(b'tampered'),
                  lambda: os.truncate({str(private)!r}, 0),
                  lambda: os.chmod({str(private)!r}, 0o777)]:
    try:
        operation()
    except PermissionError:
        blocked.append(True)
    else:
        blocked.append(False)
print(json.dumps({{'workspace_write': Path('allowed.txt').read_text() == 'workspace_output',
                  'private_operations_denied': blocked}}))
"""
    outcome = execute_command_plan(
        repository_root=workspace,
        command_plan_artifact={"command": shlex.join([sys.executable, "-c", code])},
        task_id=task_id, capability_id="shell_execute", context_hash="synthetic-isolation-fixture",
        shell_policy={"allowlist": [sys.executable]}, hub_policy_decision="allow", timeout_seconds=5,
    )
    assert outcome.success, "isolated_tool_failed"
    observation = json.loads(outcome.stdout)
    assert observation == {"workspace_write": True, "private_operations_denied": [True] * 4}, "tool_isolation_failed"
    assert private.stat().st_mode & 0o777 == 0o600, "private_store_permissions_changed"
    return {"passed": True, "workspace_write": True, "same_uid_private_read_write_truncate_chmod_denied": True,
            "uid": os.geteuid(), "fixture": "Static adversarial command through production run_shell port"}
