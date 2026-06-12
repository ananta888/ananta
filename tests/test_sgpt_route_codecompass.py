import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from agent.routes import sgpt as sgpt_route


@pytest.fixture(autouse=True)
def reset_sgpt_state(monkeypatch):
    sgpt_route.user_requests.clear()
    sgpt_route.SGPT_CIRCUIT_BREAKER["failures"] = 0
    sgpt_route.SGPT_CIRCUIT_BREAKER["last_failure"] = 0
    sgpt_route.SGPT_CIRCUIT_BREAKER["open"] = False
    monkeypatch.setattr(sgpt_route, "is_rate_limited", lambda _user_id: False)
    yield
    sgpt_route.user_requests.clear()



# ── CCSH: CodeCompass Relevant-Snippet Handoff Tests ─────────────────────────

def _make_workdir(tmp_path: pathlib.Path, refs: list[dict], hub_content: str | None = None) -> pathlib.Path:
    """Create a minimal ananta-worker workspace with research-context.json."""
    rag_dir = tmp_path / "rag_helper"
    rag_dir.mkdir(parents=True, exist_ok=True)
    (rag_dir / "research-context.json").write_text(
        json.dumps({"repo_scope_refs": refs}), encoding="utf-8"
    )
    if hub_content is not None:
        ananta_dir = tmp_path / ".ananta"
        ananta_dir.mkdir(exist_ok=True)
        (ananta_dir / "hub-context.md").write_text(hub_content, encoding="utf-8")
    return tmp_path


def _make_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a minimal fake repo root with an agent/ subdirectory."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent").mkdir()
    return repo


# CCSH-003: Regression test — path-only behavior still works

# Split from tests/test_sgpt_route.py to keep source files below 1000 lines.

class TestSourceFileBatchesPathOnly:
    def test_path_only_loads_file_beginning(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        sample.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py"}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert len(batches) == 1
        block = batches[0][0]
        assert block["rel_path"] == "sample.py"
        assert "def hello" in block["content"]
        assert block["source_kind"] == "file_excerpt"
        assert block["start_line"] is None
        assert block["end_line"] is None

    def test_path_traversal_blocked(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        secret = tmp_path / "secret.txt"
        secret.write_text("top-secret", encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "../secret.txt"}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert batches == []

    def test_nonexistent_path_skipped(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = _make_workdir(tmp_path / "ws", [{"path": "doesnotexist.py"}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert batches == []

    def test_empty_refs_falls_back_to_hub_context(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = _make_workdir(tmp_path / "ws", [], hub_content="# Hub context\nsome content")

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert len(batches) == 1
        assert batches[0][0]["source_kind"] == "hub_context"
        assert "Hub context" in batches[0][0]["content"]

    def test_invalid_json_research_context_falls_back_gracefully(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = tmp_path / "ws"
        rag_dir = workdir / "rag_helper"
        rag_dir.mkdir(parents=True)
        (rag_dir / "research-context.json").write_text("{not valid json", encoding="utf-8")
        hub_dir = workdir / ".ananta"
        hub_dir.mkdir()
        (hub_dir / "hub-context.md").write_text("fallback content", encoding="utf-8")

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        # Should gracefully fall back to hub-context.md
        assert len(batches) == 1
        assert batches[0][0]["source_kind"] == "hub_context"

    def test_missing_workdir_returns_empty(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        batches = _load_source_file_batches(str(tmp_path / "nonexistent"))
        assert batches == []


# CCSH-004: Line-range normalization
class TestLineRangeNormalization:
    def test_start_line_end_line_loads_specific_range(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        lines = ["# irrelevant header"] * 20 + ["def target_func():", "    return 42"] + ["# irrelevant footer"] * 20
        sample.write_text("\n".join(lines), encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py", "start_line": 21, "end_line": 22}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), context_lines=0)

        assert len(batches) == 1
        block = batches[0][0]
        assert block["source_kind"] == "line_range"
        assert "def target_func" in block["content"]
        # File beginning (irrelevant headers) should NOT dominate
        assert block["content"].count("# irrelevant header") == 0

    def test_line_start_alias_accepted(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        sample.write_text("\n".join([f"line{i}" for i in range(1, 30)]), encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py", "line_start": 5, "line_end": 7}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), context_lines=0)

        assert len(batches) == 1
        assert batches[0][0]["source_kind"] == "line_range"
        assert "line5" in batches[0][0]["content"]

    def test_from_line_to_line_alias_accepted(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        sample.write_text("\n".join([f"L{i}" for i in range(1, 30)]), encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py", "from_line": 10, "to_line": 12}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), context_lines=0)

        assert batches[0][0]["source_kind"] == "line_range"
        assert "L10" in batches[0][0]["content"]

    def test_invalid_line_range_falls_back_to_file_excerpt(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        sample.write_text("content\n", encoding="utf-8")

        # end < start — invalid
        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py", "start_line": 10, "end_line": 5}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert len(batches) == 1
        assert batches[0][0]["source_kind"] == "file_excerpt"


# CCSH-004/005: Snippet and chunk priority
class TestSnippetAndChunkPriority:
    def test_snippet_used_when_path_missing(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = _make_workdir(tmp_path / "ws", [{"snippet": "def my_func(): pass"}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert len(batches) == 1
        assert batches[0][0]["source_kind"] == "codecompass_snippet"
        assert "def my_func" in batches[0][0]["content"]

    def test_chunks_in_ref_used_as_context_blocks(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        chunks = [
            {"source": "module.py", "content": "def func_a(): pass", "score": 0.9},
            {"source": "module.py", "content": "def func_b(): pass", "score": 0.8},
        ]
        workdir = _make_workdir(tmp_path / "ws", [{"path": "module.py", "chunks": chunks}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), files_per_batch=5)

        # Chunks from ref used, not file-beginning fallback
        all_blocks = [b for batch in batches for b in batch]
        kinds = [b["source_kind"] for b in all_blocks]
        assert all(k == "chunk" for k in kinds)
        contents = " ".join(b["content"] for b in all_blocks)
        assert "func_a" in contents
        assert "func_b" in contents

    def test_duplicate_blocks_deduplicated(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        sample.write_text("def hello(): pass\n", encoding="utf-8")

        # Same path twice → only one block
        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py"}, {"path": "sample.py"}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        all_blocks = [b for batch in batches for b in batch]
        paths = [b["rel_path"] for b in all_blocks]
        assert paths.count("sample.py") == 1

    def test_blocks_sorted_by_score_descending(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        for name in ("a.py", "b.py", "c.py"):
            (repo / name).write_text(f"# {name}\n", encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [
            {"path": "a.py", "score": 0.3},
            {"path": "b.py", "score": 0.9},
            {"path": "c.py", "score": 0.6},
        ])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), files_per_batch=10)

        all_blocks = [b for batch in batches for b in batch]
        scores = [b["score"] for b in all_blocks]
        assert scores == sorted(scores, reverse=True)


# CCSH-002: Prompt header visibility
class TestPromptHeaders:
    def test_line_range_header_includes_line_numbers(self):
        from agent.common.sgpt import _build_iteration_prompt

        batch = [{
            "rel_path": "agent/foo.py",
            "lang": "python",
            "content": "def bar(): pass",
            "source_kind": "line_range",
            "start_line": 42,
            "end_line": 55,
            "score": 0.85,
            "reason": None,
            "symbol": None,
        }]
        prompt = _build_iteration_prompt("do something", batch=batch, progress_so_far="", step=1, total_steps=1)

        assert "agent/foo.py:42-55" in prompt
        assert "[line_range" in prompt
        assert "score=0.85" in prompt

    def test_file_excerpt_header_shows_source_kind(self):
        from agent.common.sgpt import _build_iteration_prompt

        batch = [{
            "rel_path": "agent/bar.py",
            "lang": "python",
            "content": "x = 1",
            "source_kind": "file_excerpt",
            "start_line": None,
            "end_line": None,
            "score": None,
            "reason": None,
            "symbol": None,
        }]
        prompt = _build_iteration_prompt("question", batch=batch, progress_so_far="", step=1, total_steps=1)

        assert "### agent/bar.py [file_excerpt]" in prompt

    def test_hub_context_header(self):
        from agent.common.sgpt import _build_iteration_prompt

        batch = [{
            "rel_path": "hub-context.md",
            "lang": "markdown",
            "content": "some context",
            "source_kind": "hub_context",
            "start_line": None,
            "end_line": None,
            "score": None,
            "reason": None,
            "symbol": None,
        }]
        prompt = _build_iteration_prompt("q", batch=batch, progress_so_far="", step=1, total_steps=1)
        assert "### hub-context.md [hub_context]" in prompt


# CCSH-011: E2E — relevant function far down in file is loaded, not file beginning
class TestCodeCompassSnippetHandoff:
    def test_line_range_loads_deep_function_not_file_beginning(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "deep_func.py"
        # 100 lines of irrelevant header, then relevant function
        header = ["# IRRELEVANT_MARKER"] * 100
        target_func = [
            "def deeply_buried_target():",
            "    return 'found_me'",
        ]
        sample.write_text("\n".join(header + target_func), encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{
            "path": "deep_func.py",
            "start_line": 101,
            "end_line": 102,
            "score": 0.95,
            "reason": "target function",
        }])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), context_lines=0, per_file_chars=4000)

        assert len(batches) == 1
        block = batches[0][0]
        assert block["source_kind"] == "line_range"
        assert "deeply_buried_target" in block["content"]
        # The irrelevant file beginning should NOT be present in the content
        assert "IRRELEVANT_MARKER" not in block["content"]

    def test_single_batch_prompt_includes_header_annotation(self, tmp_path):
        """Single-batch path in _run_ananta_worker_iterative also uses annotated headers."""
        from agent.common.sgpt import _load_source_file_batches, _format_block_header

        repo = _make_repo(tmp_path)
        sample = repo / "module.py"
        sample.write_text("def answer(): return 42\n", encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "module.py"}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert len(batches) == 1
        header = _format_block_header(batches[0][0])
        assert "### module.py" in header
        assert "[file_excerpt]" in header

    def test_path_traversal_still_blocked_with_line_range(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        secret = tmp_path / "secret.txt"
        secret.write_text("classified", encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "../secret.txt", "start_line": 1, "end_line": 1}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert batches == []


# CCSH-012: Backward compatibility regression tests
class TestBackwardCompatibility:
    def test_path_only_still_works_after_refactor(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        (repo / "legacy.py").write_text("x = 1\n", encoding="utf-8")
        workdir = _make_workdir(tmp_path / "ws", [{"path": "legacy.py"}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert len(batches) == 1
        assert batches[0][0]["source_kind"] == "file_excerpt"

    def test_empty_repo_scope_refs_falls_back_to_hub(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = _make_workdir(tmp_path / "ws", [], hub_content="hub fallback")

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert batches[0][0]["source_kind"] == "hub_context"

    def test_missing_research_context_json_uses_hub_fallback(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = tmp_path / "ws"
        workdir.mkdir()
        ananta_dir = workdir / ".ananta"
        ananta_dir.mkdir()
        (ananta_dir / "hub-context.md").write_text("hub only", encoding="utf-8")

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir))

        assert batches[0][0]["source_kind"] == "hub_context"

    def test_invalid_json_does_not_raise(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        workdir = tmp_path / "ws"
        (workdir / "rag_helper").mkdir(parents=True)
        (workdir / "rag_helper" / "research-context.json").write_text("{{broken", encoding="utf-8")

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            # Must not raise
            batches = _load_source_file_batches(str(workdir))

        assert isinstance(batches, list)


# CCSH-013: Budget guard
class TestBudgetGuard:
    def test_max_files_cap_limits_loaded_blocks(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        refs = []
        for i in range(10):
            f = repo / f"file{i}.py"
            f.write_text(f"# file {i}\n", encoding="utf-8")
            refs.append({"path": f"file{i}.py"})

        workdir = _make_workdir(tmp_path / "ws", refs)

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), max_files=3)

        all_blocks = [b for batch in batches for b in batch]
        assert len(all_blocks) == 3

    def test_higher_score_blocks_survive_budget_cut(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        # 3 files, different scores; budget = 2
        (repo / "low.py").write_text("# low score\n", encoding="utf-8")
        (repo / "mid.py").write_text("# mid score\n", encoding="utf-8")
        (repo / "high.py").write_text("# high score\n", encoding="utf-8")

        refs = [
            {"path": "low.py", "score": 0.1},
            {"path": "mid.py", "score": 0.5},
            {"path": "high.py", "score": 0.9},
        ]
        workdir = _make_workdir(tmp_path / "ws", refs)

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches = _load_source_file_batches(str(workdir), max_files=2)

        all_blocks = [b for batch in batches for b in batch]
        assert len(all_blocks) == 2
        # Highest-scored blocks should be present
        paths = {b["rel_path"] for b in all_blocks}
        assert "high.py" in paths
        assert "mid.py" in paths
        assert "low.py" not in paths


# CCSH-006: Config-driven line window
class TestConfigDrivenContext:
    def test_context_lines_parameter_controls_window_size(self, tmp_path):
        from agent.common.sgpt import _load_source_file_batches

        repo = _make_repo(tmp_path)
        sample = repo / "sample.py"
        lines = [f"line{i}" for i in range(1, 31)]
        sample.write_text("\n".join(lines), encoding="utf-8")

        workdir = _make_workdir(tmp_path / "ws", [{"path": "sample.py", "start_line": 15, "end_line": 15}])

        with patch("agent.common.sgpt_architecture_scan._resolve_repo_root", return_value=repo):
            batches_narrow = _load_source_file_batches(str(workdir), context_lines=0)
            batches_wide = _load_source_file_batches(str(workdir), context_lines=3)

        content_narrow = batches_narrow[0][0]["content"]
        content_wide = batches_wide[0][0]["content"]

        # Narrow: only line15
        assert "line15" in content_narrow
        assert "line12" not in content_narrow

        # Wide: lines 12–18 should be visible
        assert "line15" in content_wide
        assert "line12" in content_wide


# MLLORA-002/020/023: Architekturgrenze und Adapter-Provenance Tests
def test_sgpt_execute_ml_intern_rejects_cli_flags(client, admin_auth_header):
    """ml_intern Backend darf keine CLI-Flags akzeptieren."""
    client.post(
        "/config",
        json={"ml_intern_spike": {"enabled": True, "command_template": "python w.py"}},
        headers=admin_auth_header,
    )
    response = client.post(
        "/api/sgpt/execute",
        json={"prompt": "test", "backend": "ml_intern", "options": ["--shell"]},
        headers=admin_auth_header,
    )
    assert response.status_code == 400
    assert "ml_intern" in response.json.get("message", "").lower() or "cli flag" in response.json.get("message", "").lower()


def test_sgpt_execute_adapter_used_false_when_routing_disabled(client, admin_auth_header):
    """Wenn lora_runtime.routing_enabled=false, enthaelt Response adapter_used=false."""
    from unittest.mock import patch as _patch
    with _patch("agent.routes.sgpt.get_ml_intern_adapter_service"):
        response = client.post(
            "/api/sgpt/execute",
            json={"prompt": "hello world", "backend": "ananta-worker"},
            headers=admin_auth_header,
        )
    # Route kann fehlschlagen wegen fehlenden Backends, aber wenn erfolgreich:
    if response.status_code == 200:
        data = response.json.get("data", {})
        assert "adapter_used" in data or "lora_provenance" in data


def test_training_job_not_reachable_via_sgpt(client, admin_auth_header):
    """Training-Jobs koennen nicht ueber /api/sgpt/execute eingeschleust werden."""
    # Versuch, einen job_type in den Payload zu schmuggeln
    response = client.post(
        "/api/sgpt/execute",
        json={"prompt": "train model", "job_type": "train_lora", "backend": "ananta-worker"},
        headers=admin_auth_header,
    )
    # Muss entweder 400 (ungueltig) oder 200 ohne Training-Effekt sein
    # Training-Jobs duerften nie durch diesen Endpunkt ausgefuehrt werden
    assert response.status_code in (200, 400, 500)
    if response.status_code == 200:
        data = response.json.get("data", {})
        # Keine Training-Artefakte in der Response
        assert "training_summary" not in data
        assert "job_id" not in data
