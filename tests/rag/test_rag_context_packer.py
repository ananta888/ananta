from __future__ import annotations

from agent.services.rag_context_packer import build_rag_context_pack, format_packed_files_section


def test_rag_context_packer_embeds_top_files_within_budget(tmp_path):
    (tmp_path / "a.py").write_text("A" * 1200, encoding="utf-8")
    (tmp_path / "b.py").write_text("B" * 3000, encoding="utf-8")
    (tmp_path / "c.py").write_text("C" * 3000, encoding="utf-8")

    pack = build_rag_context_pack(
        chunks=[
            {"source": "a.py", "score": 90.0},
            {"source": "b.py", "score": 80.0},
            {"source": "c.py", "score": 70.0},
        ],
        repo_root=tmp_path,
        context_budget_chars=9000,
        reserved_chars=2500,
        max_chars_per_file=2500,
        min_initial_files=2,
        max_initial_files=3,
    )

    assert pack.included_paths[:2] == ["a.py", "b.py"]
    assert pack.included_files[0].inclusion == "full"
    assert pack.included_files[1].inclusion == "partial"
    assert pack.used_file_chars <= pack.file_budget_chars + 256

    section = format_packed_files_section(pack)
    assert "Bereits gelesene CodeCompass-Top-Treffer" in section
    assert "a.py" in section
    assert "b.py" in section


def test_rag_context_packer_skips_generated_codecompass_outputs(tmp_path):
    generated = tmp_path / "rag-helper" / "out" / "index_by_kind"
    generated.mkdir(parents=True)
    (generated / "typescript_folder_summary.jsonl").write_text("G" * 500, encoding="utf-8")
    (tmp_path / "source.py").write_text("print('source')\n", encoding="utf-8")

    pack = build_rag_context_pack(
        chunks=[
            {
                "source": "rag-helper/out/index_by_kind/typescript_folder_summary.jsonl",
                "score": 100.0,
            },
            {"source": "source.py", "score": 50.0},
        ],
        repo_root=tmp_path,
        context_budget_chars=6000,
        reserved_chars=1000,
        max_chars_per_file=2000,
        min_initial_files=1,
        max_initial_files=2,
    )

    assert pack.included_paths == ["source.py"]
    assert pack.candidate_files[0]["source"] == "rag-helper/out/index_by_kind/typescript_folder_summary.jsonl"
    assert pack.candidate_files[0]["reason"] == "generated_codecompass_output"
