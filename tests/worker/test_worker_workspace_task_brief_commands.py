"""The task brief must quote a test command the Worker policy actually allows.

A brief can arrive from any provenance (coordinator contract, ingested pilot task, goal
planner) and declares its own "Run the exact allowlisted command: ..." line. When that
command is outside ``allowlisted_test_commands`` the mutation loop cannot satisfy its own
rule (``test.run`` before ``final_answer``) and dies in ``max_iterations`` -- so the brief is
bound to the allowlist when it is written.
"""

from __future__ import annotations

from agent.services._worker_workspace_context_writer import _align_declared_test_command

ALLOWLIST = [
    "python -m pytest -q --confcutdir=tests/self_development -o addopts= tests/self_development",
    "python -m pytest -q -o addopts= tests/research_training",
]

DECLARED = (
    "Improve Ananta. Create only tests/research_training/test_growth_corpus_boundaries.py. "
    "Run the exact allowlisted command: python -m pytest -q -o addopts= "
    "tests/research_training/test_growth_corpus.py tests/research_training/test_growth_corpus_boundaries.py. "
    "Return the actual test result, then stop."
)


def test_a_narrowed_command_under_an_allowlisted_entry_is_kept():
    """Narrowing to the changed files is a valid scope: it must not be widened back.

    Running the whole profile directory would pull in unrelated failures (3 pre-existing in
    ``tests/research_training/test_bootstrap_api.py``) and block a correct proposal.
    """
    aligned = _align_declared_test_command(DECLARED, ALLOWLIST)

    assert "allowlisted command: python -m pytest -q -o addopts= " in aligned
    assert "tests/research_training/test_growth_corpus.py tests/research_training/" in aligned


def test_a_command_with_the_wrong_flags_is_repaired_to_the_allowlisted_entry():
    """Different flags are not an extension: the declaration is bound back to the entry."""
    declared = "Run exactly: python -m pytest tests/research_training/a.py. Stop."
    aligned = _align_declared_test_command(declared, ALLOWLIST)
    assert "Run exactly: python -m pytest -q -o addopts= tests/research_training." in aligned


def test_a_declared_command_that_is_already_allowlisted_is_kept():
    aligned = _align_declared_test_command(DECLARED, [
        "python -m pytest -q -o addopts= tests/research_training/test_growth_corpus.py "
        "tests/research_training/test_growth_corpus_boundaries.py",
    ])
    assert aligned == DECLARED


def test_a_declared_command_no_root_covers_is_left_for_the_loop_to_report():
    declared = "Run the exact allowlisted command: python -m pytest -q -o addopts= tests/other/x.py. Stop."
    aligned = _align_declared_test_command(declared, ["python -m pytest -q -o addopts= tests/self_development"])
    assert aligned == declared


def test_the_run_exactly_marker_is_aligned_too():
    declared = "Files: tests/research_training/a.py. Run exactly: python -m pytest tests/research_training/a.py. Stop."
    aligned = _align_declared_test_command(declared, ALLOWLIST)
    assert "Run exactly: python -m pytest -q -o addopts= tests/research_training." in aligned


def test_text_without_a_declared_command_and_without_an_allowlist_is_untouched():
    plain = "Only change these files: agent/services/thing.py"
    assert _align_declared_test_command(plain, ALLOWLIST) == plain
    assert _align_declared_test_command(DECLARED, []) == DECLARED


def test_the_allowlist_is_read_from_the_persisted_block_without_an_app_context(monkeypatch):
    """The brief is written from a path that may run without an app context.

    ``_get_agent_config()`` then yields ``{}``; the persisted block is the fallback, otherwise
    the declaration would silently stay outside the allowlist it is judged against.
    """
    import json as _json

    monkeypatch.setattr('agent.cli_backends.helpers._get_agent_config', lambda: {})

    class _ConfigRepo:
        def get_by_key(self, key):
            assert key == 'ananta_worker_workspace_mutation'
            return type('R', (), {'value_json': _json.dumps(
                {'allowlisted_test_commands': ['python -m pytest -q -o addopts= tests/research_training']})})()

    class _Registry:
        config_repo = _ConfigRepo()

    monkeypatch.setattr('agent.services.repository_registry.get_repository_registry',
                        lambda: _Registry())

    from agent.services._worker_workspace_context_writer import _allowlisted_test_commands

    assert _allowlisted_test_commands() == ['python -m pytest -q -o addopts= tests/research_training']


ENTRY = "python -m pytest -q -o addopts= tests/research_training"


def test_the_resolver_accepts_an_exact_and_a_narrowed_command():
    from agent.services.tools.test_tools import resolve_allowlisted_test_command

    assert resolve_allowlisted_test_command(ENTRY, [ENTRY]) == ENTRY
    narrowed = (
        "python -m pytest -q -o addopts= tests/research_training/test_growth_corpus.py "
        "tests/research_training/other.py"
    )
    assert resolve_allowlisted_test_command(narrowed, [ENTRY]) == ENTRY
    assert resolve_allowlisted_test_command(f"{ENTRY}/test_a.py", [ENTRY]) == ENTRY


def test_the_resolver_refuses_flags_paths_outside_the_root_and_traversal():
    from agent.services.tools.test_tools import resolve_allowlisted_test_command

    assert resolve_allowlisted_test_command(f"{ENTRY} -p no:cacheprovider", [ENTRY]) is None
    assert resolve_allowlisted_test_command(f"{ENTRY} --version", [ENTRY]) is None
    assert resolve_allowlisted_test_command(f"{ENTRY}/../../../etc/passwd", [ENTRY]) is None
    assert resolve_allowlisted_test_command("tests/agent/test_x.py", [ENTRY]) is None
    assert resolve_allowlisted_test_command("python -m pytest tests/agent/test_x.py", [ENTRY]) is None
    assert resolve_allowlisted_test_command("", [ENTRY]) is None
    assert resolve_allowlisted_test_command(f"{ENTRY}/test_a.py", []) is None


def test_test_run_executes_a_narrowed_command_but_blocks_a_disallowed_flag(tmp_path):
    """The narrowed form reaches the runner; a flag outside the entry does not."""
    from agent.services.tools.test_tools import test_run

    narrowed = test_run(
        workspace_dir=str(tmp_path),
        arguments={"command": f"{ENTRY}/test_no_such_file.py"},
        tool_call_id="tc-1",
        config={"allowlisted_test_commands": [ENTRY], "test_timeout_seconds": 60},
    )
    assert narrowed["status"] != "policy_blocked"

    flagged = test_run(
        workspace_dir=str(tmp_path),
        arguments={"command": f"{ENTRY} -p no:cacheprovider"},
        tool_call_id="tc-2",
        config={"allowlisted_test_commands": [ENTRY], "test_timeout_seconds": 60},
    )
    assert flagged["status"] == "policy_blocked"
    assert flagged["error"] == "command_not_allowlisted"
