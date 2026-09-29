from __future__ import annotations

from pathlib import Path

import pytest

from agent.services.knowledge_hygiene.writeback import (
    KnowledgeWritebackError,
    ObsidianMarkdownWritebackAdapter,
    content_sha256,
)
from ananta_contracts.knowledge_hygiene import build_correction_proposal


def _proposal(path: Path, content: str, proposed: str):
    return build_correction_proposal(
        correction_id="correction-1",
        project_id="project-a",
        conflict_id="KHC_" + "a" * 24,
        source_id="SRC_0001",
        source_revision="rev-1",
        source_locator=str(path),
        base_content_sha256=content_sha256(content),
        proposed_content=proposed,
        proposed_by_run_id="RUN_0001",
        created_at=1.0,
    )


def test_obsidian_writeback_is_hash_bound_atomic_and_backed_up(tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("before\n", encoding="utf-8")
    adapter = ObsidianMarkdownWritebackAdapter(allowed_roots=(vault,), max_patch_bytes=1024)
    proposal = _proposal(note, "before\n", "after\n")

    preview = adapter.preview(proposal)
    receipt = adapter.apply(proposal)

    assert preview["status"] == "clean"
    assert note.read_text(encoding="utf-8") == "after\n"
    assert Path(receipt.backup_path).read_text(encoding="utf-8") == "before\n"


def test_obsidian_writeback_rejects_revision_race(tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "note.md"
    note.write_text("before\n", encoding="utf-8")
    adapter = ObsidianMarkdownWritebackAdapter(allowed_roots=(vault,), max_patch_bytes=1024)
    proposal = _proposal(note, "before\n", "after\n")
    note.write_text("concurrent\n", encoding="utf-8")

    with pytest.raises(KnowledgeWritebackError, match="source_revision_race"):
        adapter.apply(proposal)


def test_obsidian_writeback_rejects_traversal_and_symlink(tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    adapter = ObsidianMarkdownWritebackAdapter(allowed_roots=(vault,), max_patch_bytes=1024)

    with pytest.raises(KnowledgeWritebackError, match="outside_allowed_roots"):
        adapter.preview(_proposal(outside, "outside\n", "changed\n"))

    link = vault / "link.md"
    link.symlink_to(outside)
    with pytest.raises(KnowledgeWritebackError, match="symlink_writeback_forbidden"):
        adapter.preview(_proposal(link, "outside\n", "changed\n"))
