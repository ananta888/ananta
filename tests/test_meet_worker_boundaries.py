"""Narrow architecture regressions, not a malicious-code sandbox proof."""

from pathlib import Path

import pytest

from scripts.check_meet_worker_boundaries import audit, main, source_findings

pytestmark = pytest.mark.timeout(30)


@pytest.mark.parametrize(
    "source",
    [
        "import agent.services.meet_dialog_service as hub",
        "from agent.services import task_queue_service as queue",
        "from agent import common",
        "import importlib as loader\nloader.import_module('agent.services.tasks')",
        "from importlib import import_module as load\nload('agent.services.tasks')",
        "__import__('agent')",
        "import builtins as b\nb.__import__('agent.services.tasks')",
    ],
)
def test_rejects_direct_and_literal_dynamic_hub_imports(source):
    assert any(code == "meet_worker_hub_import" for _, code in source_findings(source))


@pytest.mark.parametrize(
    "source",
    [
        "hub.ingest_task(task_id='synthetic')",
        "transport.start_dialog(assignment)",
        "async def work():\n    get_task_queue_service().ingest_task()",
        "from external import ingest_task as submit\nsubmit()",
    ],
)
def test_rejects_explicit_task_ownership_even_through_an_alias(source):
    assert any(code == "meet_worker_task_ownership" for _, code in source_findings(source))


def test_comments_strings_and_local_async_execution_are_not_task_delegation():
    source = """
import asyncio
from ananta_contracts.meet_dialog import validate_assignment
from worker.meet_media.dialog_client import HubDialogClient
from agentish import harmless
# import agent.services; hub.ingest_task()
description = "transport.start_dialog()"
async def execute():
    asyncio.create_task(receive_one_assigned_frame())
"""
    assert source_findings(source) == []


def test_malformed_source_has_only_a_fixed_rule_code():
    assert source_findings("private_invalid = (") == [(1, "meet_worker_source_invalid")]


def test_cli_reports_relative_locations_without_executing_audited_source(tmp_path, capsys):
    package = tmp_path / "worker/meet_media"
    package.mkdir(parents=True)
    (package / "bad.py").write_text("raise RuntimeError('must never execute')\nimport agent.services\n")
    assert main(["--root", str(tmp_path)]) == 1
    assert capsys.readouterr().out == "worker/meet_media/bad.py:2: meet_worker_hub_import\n"


@pytest.mark.parametrize("kind", ["missing", "oversize", "symlink"])
def test_invalid_scan_input_is_bounded(tmp_path, kind):
    if kind != "missing":
        package = tmp_path / "worker/meet_media"
        package.mkdir(parents=True)
        if kind == "oversize":
            (package / "large.py").write_bytes(b"#" * 262145)
        else:
            target = tmp_path / "outside.py"
            target.write_text("# must not be followed")
            (package / "linked.py").symlink_to(target)
    with pytest.raises(ValueError, match="meet_worker_"):
        audit(tmp_path)


def test_actual_standalone_package_preserves_control_plane_boundary():
    count, findings = audit(Path(__file__).resolve().parents[1])
    assert count > 0
    assert findings == []
