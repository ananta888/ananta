"""CCOMP-RESOLVE-1: Tests for CodeCompassCandidateResolver per-path caps and
Source-First selector.

Reproduces the failure mode observed when a user asks
"erkläre mir den codecompass":
- Without caps, files with hundreds of details records (VoxtralOfflinePlugin.java,
  docker-compose.test.yml, .github workflows) flood the ranking.
- Without source-first, test/, docs/, .github/, frontend-angular/ outrank the
  actual source-of-truth files (worker/retrieval/codecompass_*.py,
  agent/services/codecompass_*.py).
- Without broad_token cap, every shared short token ("re", "de") is enough
  for a large file to dominate.
- Without a path-shape filter, relation records with Java type symbols
  ("String", "void", "File") pollute the candidate set.
- Without stem-tokenization, a snake_case filename like
  "codecompass_candidate_resolver.py" never gets a stem-match boost for
  the natural-language query token "codecompass".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.retrieval.codecompass_candidate_resolver import (
    CodeCompassCandidateResolver,
    ResolverConfig,
    _classify_path,
    _is_source_path,
    _looks_like_path,
    _source_path_multiplier,
    _stem_boost,
    _stem_tokens,
)
from worker.retrieval.codecompass_query_parser import parse_codecompass_query


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _make_manifest(out: Path, *, present: set[str]) -> dict:
    """Build a manifest with all outputs present (or missing).
    The resolver only loads files that are listed in the manifest, so we
    must declare every output we write to disk.
    """
    return {
        "schema": "codecompass_output_manifest.v1",
        "outputs": {
            name: {
                "path": f"{name}.jsonl",
                "record_count": 0,
                "size_bytes": 0,
            }
            for name in present
        },
    }


def _seed_minimal_codecompass_output(
    out: Path,
    *,
    details_by_file: dict[str, int],
    context_by_file: dict[str, int],
    relations: list[dict] | None = None,
    graph_nodes_by_file: dict[str, int] | None = None,
) -> None:
    """Write a minimal CodeCompass output directory that the resolver can read.

    The records carry `_provenance.output_kind` set explicitly, so the
    resolver picks them up regardless of file-name inference.
    """
    out.mkdir(parents=True, exist_ok=True)

    index_records = []
    for f in set(details_by_file) | set(context_by_file):
        index_records.append(
            {
                "id": f"idx-{f}",
                "file": f,
                "kind": "python_file",
                "embedding_text": f"Python file at {f}",
                "_provenance": {"output_kind": "index", "manifest_hash": "test"},
            }
        )
    details_records = []
    for f, n in details_by_file.items():
        for i in range(n):
            details_records.append(
                {
                    "id": f"det-{f}-{i}",
                    "file": f,
                    "kind": "method",
                    "name": f"repair_handler_{i}",
                    "embedding_text": f"Method repair_handler_{i} in {f}",
                    "_provenance": {"output_kind": "details", "manifest_hash": "test"},
                }
            )
    context_records = []
    for f, n in context_by_file.items():
        for i in range(n):
            context_records.append(
                {
                    "id": f"ctx-{f}-{i}",
                    "file": f,
                    "kind": "md_section",
                    "heading": f"Section {i}",
                    "content": f"Repair session content for {f}",
                    "_provenance": {"output_kind": "context", "manifest_hash": "test"},
                }
            )
    relations_records = relations or []
    graph_nodes_records = []
    for f, n in (graph_nodes_by_file or {}).items():
        for i in range(n):
            graph_nodes_records.append(
                {
                    "id": f"gn-{f}-{i}",
                    "file": f,
                    "kind": "python_module",
                    "_provenance": {"output_kind": "graph_nodes", "manifest_hash": "test"},
                }
            )

    present = {"index", "details", "context"}
    if relations_records:
        present.add("relations")
    if graph_nodes_records:
        present.add("graph_nodes")

    manifest = _make_manifest(out, present=present)
    for name, recs in (
        ("index", index_records),
        ("details", details_records),
        ("context", context_records),
        ("relations", relations_records),
        ("graph_nodes", graph_nodes_records),
    ):
        if name not in present:
            continue
        _write_jsonl(out / f"{name}.jsonl", recs)
        manifest["outputs"][name]["record_count"] = len(recs)
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


# ---------------------------------------------------------------------------
# Path-shape helpers
# ---------------------------------------------------------------------------



# Split from tests/test_codecompass_candidate_resolver_caps.py to keep source files below 1000 lines.

class TestResolveWithMode:
    """End-to-end: the resolve() function honours ResolverConfig."""

    def test_default_mode_excludes_tests(self, tmp_path) -> None:
        out = tmp_path / "cc"
        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                "agent/services/hub_loader.py": 5,
                "tests/test_hub_loader.py": 5,
            },
            context_by_file={
                "agent/services/hub_loader.py": 5,
                "tests/test_hub_loader.py": 5,
            },
        )
        cands = CodeCompassCandidateResolver().resolve(
            question="erkläre hub", output_dir=out,
        )
        paths = [c["path"] for c in cands]
        # tests/ must be filtered.
        assert not any("tests/" in p for p in paths), f"default mode must filter tests; got: {paths}"
        # source must be present.
        assert any("hub_loader.py" in p and "tests/" not in p for p in paths)

    def test_include_test_paths_opt_in_keeps_tests(self, tmp_path) -> None:
        out = tmp_path / "cc"
        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                "agent/services/hub_loader.py": 5,
                "tests/test_hub_loader.py": 5,
            },
            context_by_file={
                "agent/services/hub_loader.py": 5,
                "tests/test_hub_loader.py": 5,
            },
        )
        mode = ResolverConfig(include_test_paths=True)
        cands = CodeCompassCandidateResolver().resolve(
            question="erkläre hub", output_dir=out, mode=mode,
        )
        paths = [c["path"] for c in cands]
        # tests/ must now be present.
        assert any("tests/test_hub_loader.py" in p for p in paths), f"opt-in must keep tests; got: {paths}"
        # And source still wins on score (× 1.0 vs × 0.7 secondary).
        scores = {c["path"]: c["score"] for c in cands}
        if "agent/services/hub_loader.py" in scores and "tests/test_hub_loader.py" in scores:
            assert scores["agent/services/hub_loader.py"] > scores["tests/test_hub_loader.py"], (
                f"source must outrank tests even when both are opted in; "
                f"src={scores['agent/services/hub_loader.py']:.2f} test={scores['tests/test_hub_loader.py']:.2f}"
            )

    def test_include_docs_opt_in_keeps_readme(self, tmp_path) -> None:
        out = tmp_path / "cc"
        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                "agent/services/hub_loader.py": 3,
                "README.md": 3,
            },
            context_by_file={
                "agent/services/hub_loader.py": 3,
                "README.md": 3,
            },
        )
        # Default: README filtered.
        cands = CodeCompassCandidateResolver().resolve(
            question="hub", output_dir=out,
        )
        assert "README.md" not in [c["path"] for c in cands], "default mode must filter README.md"
        # Opt-in: README appears.
        cands2 = CodeCompassCandidateResolver().resolve(
            question="hub", output_dir=out,
            mode=ResolverConfig(include_docs=True),
        )
        paths2 = [c["path"] for c in cands2]
        assert "README.md" in paths2, f"include_docs=True must keep README.md; got: {paths2}"

    def test_include_workflows_opt_in_keeps_compose(self, tmp_path) -> None:
        out = tmp_path / "cc"
        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                "agent/services/hub_loader.py": 3,
                "docker-compose.yml": 3,
            },
            context_by_file={
                "agent/services/hub_loader.py": 3,
                "docker-compose.yml": 3,
            },
        )
        cands_default = CodeCompassCandidateResolver().resolve(
            question="hub", output_dir=out,
        )
        assert "docker-compose.yml" not in [c["path"] for c in cands_default]
        cands_workflow = CodeCompassCandidateResolver().resolve(
            question="hub", output_dir=out,
            mode=ResolverConfig(include_workflows=True),
        )
        paths = [c["path"] for c in cands_workflow]
        assert "docker-compose.yml" in paths

    def test_env_var_overrides_default(self, tmp_path, monkeypatch) -> None:
        out = tmp_path / "cc"
        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                "agent/services/hub_loader.py": 3,
                "tests/test_hub_loader.py": 3,
            },
            context_by_file={
                "agent/services/hub_loader.py": 3,
                "tests/test_hub_loader.py": 3,
            },
        )
        # Set env var → tests are opted in via ResolverConfig.from_env().
        monkeypatch.setenv("ANANTA_CODECOMPASS_INCLUDE_TEST_PATHS", "1")
        cands = CodeCompassCandidateResolver().resolve(
            question="hub", output_dir=out,
        )
        paths = [c["path"] for c in cands]
        assert any("tests/test_hub_loader.py" in p for p in paths), (
            f"env var must opt tests in; got: {paths}"
        )

    def test_all_includes_match_old_behavior(self, tmp_path) -> None:
        """When ALL includes are flipped on, the resolver behaves like
        the pre-ResolverConfig version (everything gets scored, secondary
        kinds get × 0.7 instead of × 0.2/× 0.3). The exact multiplier
        changed, but the inclusion logic is the key invariant.
        """
        out = tmp_path / "cc"
        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                "agent/services/hub_loader.py": 3,
                "tests/test_hub_loader.py": 3,
                "README.md": 3,
                "docker-compose.yml": 3,
            },
            context_by_file={
                "agent/services/hub_loader.py": 3,
                "tests/test_hub_loader.py": 3,
                "README.md": 3,
                "docker-compose.yml": 3,
            },
        )
        cands = CodeCompassCandidateResolver().resolve(
            question="hub",
            output_dir=out,
            mode=ResolverConfig(
                include_source=True,
                include_test_paths=True,
                include_docs=True,
                include_workflows=True,
                include_third_party=True,
            ),
        )
        paths = {c["path"] for c in cands}
        assert "agent/services/hub_loader.py" in paths
        assert "tests/test_hub_loader.py" in paths
        assert "README.md" in paths
        assert "docker-compose.yml" in paths

    def test_stem_boost_applies_to_test_paths_when_opted_in(self, tmp_path) -> None:
        """When tests are opted in, a test file with stem-token match
        (`test_hub_loader.py` for query 'hub') should score above a test
        file without stem-token match. This ensures the stem-boost
        mechanic is mode-aware, not source-only.

        Regression: stem_boost was previously hardcoded to source paths
        only, so opted-in test paths with stem match were silently
        demoted to × 1.0 (and would never reach top-40 because source
        paths outranked them via × 1.0 × 1.5 stem_boost).
        """
        out = tmp_path / "cc"
        # Two test files: one has 'hub' in stem, one doesn't.
        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                "tests/test_hub_loader.py": 5,  # has 'hub' in stem
                "tests/test_random_thing.py": 5,  # no hub-token
            },
            context_by_file={
                "tests/test_hub_loader.py": 5,
                "tests/test_random_thing.py": 5,
            },
        )
        mode = ResolverConfig(include_test_paths=True)
        cands = CodeCompassCandidateResolver().resolve(
            question="hub", output_dir=out, mode=mode,
        )
        scores = {c["path"]: c["score"] for c in cands}
        if "tests/test_hub_loader.py" in scores and "tests/test_random_thing.py" in scores:
            assert scores["tests/test_hub_loader.py"] > scores["tests/test_random_thing.py"], (
                f"test_hub_loader.py should outrank test_random_thing.py via stem_boost; "
                f"got hub={scores['tests/test_hub_loader.py']:.2f} "
                f"random={scores['tests/test_random_thing.py']:.2f}"
            )

    def test_default_mode_excludes_thirdparty(self) -> None:
        """frontend-angular/ is classified as third-party. The default
        ResolverConfig must filter it out."""
        cfg = ResolverConfig()
        assert cfg.accepts("frontend-angular/src/app/services/hub-api-core.service.ts") is False
        assert cfg.accepts("client_surfaces/blender/addon/tasks.py") is False
        assert cfg.accepts("client_surfaces/vscode_extension/src/extension.ts") is False
        # But opted-in makes them appear.
        cfg2 = ResolverConfig(include_third_party=True)
        assert cfg2.accepts("frontend-angular/src/app/services/hub-api-core.service.ts") is True
        assert cfg2.accepts("client_surfaces/blender/addon/tasks.py") is True

    def test_source_still_outranks_test_with_stem_match(self, tmp_path) -> None:
        """Even with include_test_paths=True, a source file with stem
        match outranks a test file with stem match — Source-First is
        preserved when the user opts tests in. The source multiplier
        (× 1.0) and stem_boost (× 1.5) multiply to 1.5, while the test
        gets × 0.7 × 1.5 = 1.05.
        """
        out = tmp_path / "cc"
        # Both files have 'hub' in stem and identical raw_score.
        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                "agent/services/hub_loader.py": 5,  # source + stem match
                "tests/test_hub_loader.py": 5,  # test + stem match
            },
            context_by_file={
                "agent/services/hub_loader.py": 5,
                "tests/test_hub_loader.py": 5,
            },
        )
        mode = ResolverConfig(include_test_paths=True)
        cands = CodeCompassCandidateResolver().resolve(
            question="hub", output_dir=out, mode=mode,
        )
        scores = {c["path"]: c["score"] for c in cands}
        assert scores["agent/services/hub_loader.py"] > scores["tests/test_hub_loader.py"], (
            f"Source-First: source must outrank test even with stem match; "
            f"src={scores['agent/services/hub_loader.py']:.2f} "
            f"test={scores['tests/test_hub_loader.py']:.2f}"
        )
        # And the ratio should be exactly 1.0/0.7 = 1.4286.
        ratio = scores["agent/services/hub_loader.py"] / scores["tests/test_hub_loader.py"]
        assert abs(ratio - 1.0 / 0.7) < 0.05, f"ratio {ratio:.3f} should be ~{1/0.7:.3f}"
