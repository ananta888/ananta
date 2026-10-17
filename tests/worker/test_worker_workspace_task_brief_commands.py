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


def test_a_declared_command_under_an_allowlisted_root_is_rewritten():
    aligned = _align_declared_test_command(DECLARED, ALLOWLIST)

    assert "allowlisted command: python -m pytest -q -o addopts= tests/research_training." in aligned
    assert "tests/research_training/test_growth_corpus.py tests/" not in aligned
    assert aligned.startswith("Improve Ananta.") and aligned.endswith("then stop.")


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
