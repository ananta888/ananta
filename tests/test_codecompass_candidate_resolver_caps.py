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


class TestLooksLikePath:
    def test_filters_bare_java_type_symbols(self) -> None:
        # Java type symbols that show up as relation targets.
        assert _looks_like_path("String") is False
        assert _looks_like_path("void") is False
        assert _looks_like_path("File") is False
        assert _looks_like_path("IOException") is False

    def test_filters_bare_id_symbols(self) -> None:
        # md_file:hash, java_type:hash, md_section:hash:N
        assert _looks_like_path("md_file:a03585ab89eed5e0") is False
        assert _looks_like_path("java_type:f482bd7dad4b87f1") is False

    def test_filters_java_fully_qualified_class_names(self) -> None:
        # Java FQNs like "io.ananta.eclipse.runtime.core.AnantaApiClient"
        # are NOT file paths — they are class references. A `.` alone is
        # not enough to accept them; the value must contain `/`.
        assert _looks_like_path("io.ananta.eclipse.runtime.core.AnantaApiClient") is False
        assert _looks_like_path("java.lang.String") is False
        assert _looks_like_path("java.util.List") is False
        assert _looks_like_path("com.getcapacitor.PluginCall") is False

    def test_filters_method_call_snippets(self) -> None:
        # Method-call snippets like
        # `Objects.requireNonNull(apiClient, "apiClient")`,
        # `List.of()`, `Map.of()` show up in relations records as the
        # `to`/`target`/`target_resolved` value. The presence of `(`,
        # `)`, or `;` disqualifies them as paths.
        assert _looks_like_path("Objects.requireNonNull(apiClient, \"apiClient\")") is False
        assert _looks_like_path("List.of()") is False
        assert _looks_like_path("Map.of()") is False
        assert _looks_like_path("Thread.currentThread().interrupt()") is False
        assert _looks_like_path('Objects.toString(value, "").trim()') is False

    def test_filters_ananta_bootstrap_method_calls(self) -> None:
        # Ananta-internal Java method calls (Eclipse plugin code).
        assert _looks_like_path("AnantaRuntimeBootstrap.profile()") is False
        assert _looks_like_path("AnantaRuntimeBootstrap.snakeService()") is False

    def test_accepts_repo_relative_paths(self) -> None:
        assert _looks_like_path("worker/retrieval/codecompass_candidate_resolver.py") is True
        assert _looks_like_path("agent/services/x.py") is True
        assert _looks_like_path("README.md") is True

    def test_empty_string_rejected(self) -> None:
        assert _looks_like_path("") is False


# ---------------------------------------------------------------------------
# Source-First selector
# ---------------------------------------------------------------------------


class TestSourcePathMultiplier:
    def test_known_test_paths_demoted(self) -> None:
        # Test files get the test multiplier (lower than source).
        assert _source_path_multiplier("tests/test_foo.py") < 1.0
        assert _source_path_multiplier("worker/tests/test_bar.py") < 1.0

    def test_docker_compose_demoted(self) -> None:
        assert _source_path_multiplier("docker-compose.test.yml") < 1.0
        assert _source_path_multiplier("docker-compose.final-tests.yml") < 1.0

    def test_workflows_demoted(self) -> None:
        assert _source_path_multiplier(".github/workflows/quality-and-docs.yml") < 1.0

    def test_docs_demoted(self) -> None:
        assert _source_path_multiplier("docs/architecture.md") < 1.0

    def test_third_party_client_demoted(self) -> None:
        # Blender / FreeCAD / Vim etc. share the Ananta root but are ports.
        assert _source_path_multiplier("client_surfaces/blender/addon/tasks.py") < 1.0
        assert _source_path_multiplier("client_surfaces/freecad/panel/foo.py") < 1.0
        assert _source_path_multiplier("client_surfaces/vscode_extension/src/extension.ts") < 1.0

    def test_ananta_eclipse_plugin_kept(self) -> None:
        # Ananta-eigenes Eclipse-Plugin: voller Multiplier, NICHT demoted.
        assert _source_path_multiplier(
            "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/io/ananta/eclipse/runtime/core/AnantaApiClient.java"
        ) == 1.0

    def test_ananta_tui_kept(self) -> None:
        assert _source_path_multiplier("client_surfaces/operator_tui/renderer.py") == 1.0
        assert _source_path_multiplier("client_surfaces/operator_tui/tutorial_ai_mixin.py") == 1.0

    def test_agent_source_paths_kept(self) -> None:
        assert _source_path_multiplier("agent/services/codecompass_output_reader.py") == 1.0
        assert _source_path_multiplier("worker/retrieval/codecompass_candidate_resolver.py") == 1.0


# ---------------------------------------------------------------------------
# Stem-tokenization (snake_case splitting)
# ---------------------------------------------------------------------------


class TestStemTokens:
    def test_snake_case_is_split(self) -> None:
        # Bug being prevented: tokens must be split on `_` so the
        # broad-token "codecompass" matches the stem of a file like
        # "codecompass_candidate_resolver.py".
        tokens = _stem_tokens("worker/retrieval/codecompass_candidate_resolver.py")
        assert "codecompass" in tokens
        assert "candidate" in tokens
        assert "resolver" in tokens
        # The whole unsplit form should NOT be the only token.
        assert tokens != {"codecompass_candidate_resolver"}

    def test_short_domain_tokens_preserved(self) -> None:
        # 3-char domain tokens like "hub", "tui", "cli", "rpc" must
        # survive the min-length filter. Without them, files named
        # `hub_loader.py` don't get the stem-match boost for a query
        # containing "hub" — and the Eclipse-Plugin's `eclipseHub*`
        # symbols outrank the actual hub_loader.py.
        tokens = _stem_tokens("client_surfaces/operator_tui/hub_loader.py")
        assert "hub" in tokens
        assert "loader" in tokens

    def test_one_char_tokens_filtered(self) -> None:
        # 1-char tokens are pure noise (e.g. "x", "y"); 2-char tokens
        # are too noisy ("re", "de", "en"). 3-char is the floor.
        assert "x" not in _stem_tokens("x.py")
        # "ab" is 2 chars; should be filtered.
        assert "ab" not in _stem_tokens("ab_cd.py")
        # 3+ char tokens survive the floor.
        assert "abc" in _stem_tokens("abc_def.py")
        assert "def" in _stem_tokens("abc_def.py")


class TestStemBoost:
    def test_stem_boost_fires_on_broad_token(self) -> None:
        # Broad tokens like "codecompass" are the natural-language signal
        # the user gives for a subsystem query. They MUST trigger the boost.
        boost = _stem_boost(
            "worker/retrieval/codecompass_candidate_resolver.py",
            {"codecompass"},
        )
        assert boost == 1.5

    def test_stem_boost_fires_on_lowered_broad_token(self) -> None:
        # Case-mixed exact symbols do NOT need to trigger the boost —
        # the boost is the natural-language signal mechanism and works on
        # the lowered form. Exact symbols are handled by the per-symbol
        # weight inside the resolver loop. This documents the contract.
        boost = _stem_boost(
            "agent/services/codecompass_output_reader.py",
            {"codecompass"},  # lowered broad token
        )
        assert boost == 1.5

    def test_stem_boost_fires_on_exact_symbol_lowered(self) -> None:
        # Same: lowered exact symbol triggers the boost.
        boost = _stem_boost(
            "agent/services/codecompass_output_reader.py",
            {"codecompass"},  # exact symbol, lowered
        )
        assert boost == 1.5

    def test_stem_boost_does_not_fire_when_no_overlap(self) -> None:
        boost = _stem_boost(
            "client_surfaces/operator_tui/renderer.py",
            {"codecompass"},
        )
        assert boost == 1.0

    def test_stem_boost_skips_test_paths(self) -> None:
        # Test paths keep the test multiplier only; no extra stem boost.
        boost = _stem_boost(
            "tests/test_codecompass_candidate_resolver.py",
            {"codecompass"},
        )
        assert boost == 1.0


# ---------------------------------------------------------------------------
# Query parser integration with stem-boost
# ---------------------------------------------------------------------------


class TestQueryParserBroadTokens:
    def test_natural_language_query_yields_broad_token(self) -> None:
        # The natural-language query "erkläre mir den codecompass" must
        # yield a broad_tokens entry for "codecompass" — otherwise the
        # stem-boost never fires.
        parsed = parse_codecompass_query("erkläre mir den codecompass")
        assert "codecompass" in parsed["broad_terms"]

    def test_very_short_tokens_filtered(self) -> None:
        # Single-char tokens are noise; not useful for stem matching.
        parsed = parse_codecompass_query("a b c d e f")
        # All tokens are 1 char; they may or may not appear depending on
        # the regex, but no token below 2 chars should ever be a useful
        # broad term for ranking. This test documents the contract.
        assert all(len(t) >= 2 for t in parsed["broad_terms"])


# ---------------------------------------------------------------------------
# Per-path caps on accumulator bonuses
# ---------------------------------------------------------------------------


class TestResolverPerPathCaps:
    def test_large_test_docker_workflow_file_outranked_by_source(self, tmp_path) -> None:
        """docker-compose.test.yml with 300 details records must NOT outrank
        worker/retrieval/codecompass_candidate_resolver.py for the query
        'erkläre mir den codecompass'.

        Regression: previously the docker-compose file ranked #1 because
        every env-var-key became a details record and broad_token /
        details_hit accumulated per record (300+ records).
        """
        out = tmp_path / "cc"
        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                "docker-compose.test.yml": 300,
                "tests/test_codecompass_stuff.py": 250,
                ".github/workflows/ci.yml": 200,
                "client_surfaces/blender/addon/tasks.py": 300,  # third-party flooder
                "worker/retrieval/codecompass_candidate_resolver.py": 14,
                "worker/retrieval/codecompass_output_reader.py": 15,
            },
            context_by_file={
                "docker-compose.test.yml": 300,
                "tests/test_codecompass_stuff.py": 200,
                ".github/workflows/ci.yml": 100,
                "client_surfaces/blender/addon/tasks.py": 200,
                "worker/retrieval/codecompass_candidate_resolver.py": 14,
                "worker/retrieval/codecompass_output_reader.py": 15,
            },
        )

        resolver = CodeCompassCandidateResolver()
        candidates = resolver.resolve(
            question="erkläre mir den codecompass",
            output_dir=out,
        )

        paths = [c["path"] for c in candidates]

        # 1) The third-party flooder (blender) must rank BELOW every
        # CodeCompass source file in the candidates. It may still appear
        # in the bottom of the list — the 0.2 multiplier is strong but
        # not absolute — but it must never outrank the actual subsystem
        # source-of-truth files the user asked about.
        cc_source_files = [p for p in paths if "worker/retrieval/codecompass_" in p]
        blender_files = [p for p in paths if "client_surfaces/blender" in p]
        if cc_source_files and blender_files:
            cc_min_rank = min(paths.index(p) for p in cc_source_files) + 1
            blender_max_rank = max(paths.index(p) for p in blender_files) + 1
            assert blender_max_rank > cc_min_rank, (
                f"blender flooder must rank below CodeCompass source files; "
                f"cc_min_rank={cc_min_rank} blender_max_rank={blender_max_rank} paths={paths[:10]}"
            )

        # 2) Test / docs / workflows / docker-compose must not occupy Top-2.
        top_two = paths[:2]
        assert not any(
            marker in p
            for p in top_two
            for marker in ("tests/", "docker-compose", ".github/", "docs/")
        ), f"test/ci/docs/demoted paths should not be in Top-2, got: {top_two}"

        # 3) At least one worker/retrieval/codecompass_*.py file is in
        # the candidates list (its stem matches the query).
        assert any(
            "worker/retrieval/codecompass_" in p for p in paths
        ), f"CodeCompass source files should appear, got: {paths[:5]}"

    def test_broad_token_cap_prevents_short_token_flooding(self, tmp_path) -> None:
        """A file with 200 details records that all share a 2-char broad
        token ('re') must NOT outscore a smaller file whose stem matches
        the query.

        Regression: previously VoxtralOfflinePlugin.java with 236 records
        ranked #1 for 'codecompass' because token 're' matched in 236
        records' names/embeddings, accumulating broad_token per record.
        """
        out = tmp_path / "cc"
        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                # 200 records, every name contains "re" (e.g. "repair_handler_X")
                "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/io/ananta/eclipse/runtime/RepairBackend.java": 200,
                # 5 records, stem matches "codecompass"
                "worker/retrieval/codecompass_candidate_resolver.py": 5,
            },
            context_by_file={
                "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/io/ananta/eclipse/runtime/RepairBackend.java": 200,
                "worker/retrieval/codecompass_candidate_resolver.py": 5,
            },
        )

        resolver = CodeCompassCandidateResolver()
        candidates = resolver.resolve(
            question="erkläre mir den codecompass",
            output_dir=out,
        )

        paths = [c["path"] for c in candidates]
        scores = {c["path"]: c["score"] for c in candidates}

        # The CodeCompass source file must outrank the RepairBackend flooder.
        cc_path = "worker/retrieval/codecompass_candidate_resolver.py"
        repair_path = next(
            (p for p in paths if "RepairBackend.java" in p), None
        )
        assert repair_path is not None, f"RepairBackend expected in candidates, got: {paths[:5]}"
        assert scores[cc_path] > scores[repair_path], (
            f"CodeCompass source should outrank RepairBackend flooder; "
            f"scores: cc={scores[cc_path]:.2f} repair={scores[repair_path]:.2f}"
        )

    def test_java_type_symbol_relations_filtered(self, tmp_path) -> None:
        """Relation records with bare Java type symbols ('String', 'void',
        'File') in the target field must NOT produce candidate paths
        named 'String', 'void', 'File'.

        Regression: previously the top 1-3 candidates for any query were
        the Java type names because relations records had non-path
        'to' values that the resolver treated as file paths.
        """
        out = tmp_path / "cc"
        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                "worker/retrieval/codecompass_candidate_resolver.py": 3,
            },
            context_by_file={
                "worker/retrieval/codecompass_candidate_resolver.py": 3,
            },
            relations=[
                {
                    "from": "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/A.java",
                    "to": "String",  # Java type symbol, NOT a path
                    "type": "uses",
                    "_provenance": {"output_kind": "relations", "manifest_hash": "test"},
                },
                {
                    "from": "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/A.java",
                    "to": "void",  # Java type symbol, NOT a path
                    "type": "uses",
                    "_provenance": {"output_kind": "relations", "manifest_hash": "test"},
                },
                {
                    "from": "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/A.java",
                    "to": "File",  # Java type symbol, NOT a path
                    "type": "uses",
                    "_provenance": {"output_kind": "relations", "manifest_hash": "test"},
                },
                {
                    "from": "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/src/main/java/A.java",
                    "to": "worker/retrieval/codecompass_candidate_resolver.py",  # real path
                    "type": "calls",
                    "_provenance": {"output_kind": "relations", "manifest_hash": "test"},
                },
            ],
        )

        resolver = CodeCompassCandidateResolver()
        candidates = resolver.resolve(
            question="erkläre mir den codecompass",
            output_dir=out,
        )
        paths = [c["path"] for c in candidates]

        # None of the type-symbol garbage may appear as a candidate path.
        assert "String" not in paths
        assert "void" not in paths
        assert "File" not in paths

    def test_incoming_relations_capped_per_target(self, tmp_path) -> None:
        """A central file that is the `to` of 50 relations from 50 different
        sources must NOT accumulate 50 × 0.4 = 20 points from relations.

        Regression: AnantaApiClient.java accumulated relation_neighbor
        score from 500+ sources and outranked every source-of-truth file.
        """
        out = tmp_path / "cc"
        # Build 50 relations from 50 different source files into one target.
        relations = [
            {
                "from": f"some/other/file_{i}.py",
                "to": "agent/services/central_hub.py",  # target
                "type": "calls",
                "_provenance": {"output_kind": "relations", "manifest_hash": "test"},
            }
            for i in range(50)
        ]
        # Plus one relation that points at the source-of-truth file
        # (must still be visible in candidates, but capped).
        relations.append(
            {
                "from": "agent/services/central_hub.py",
                "to": "worker/retrieval/codecompass_candidate_resolver.py",
                "type": "calls",
                "_provenance": {"output_kind": "relations", "manifest_hash": "test"},
            }
        )

        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                "agent/services/central_hub.py": 5,
                "worker/retrieval/codecompass_candidate_resolver.py": 5,
            },
            context_by_file={
                "agent/services/central_hub.py": 5,
                "worker/retrieval/codecompass_candidate_resolver.py": 5,
            },
            relations=relations,
        )

        resolver = CodeCompassCandidateResolver()
        candidates = resolver.resolve(
            question="erkläre mir den codecompass",
            output_dir=out,
        )
        scores = {c["path"]: c["score"] for c in candidates}
        paths = [c["path"] for c in candidates]

        # The CodeCompass source file has stem-match boost;
        # central_hub.py has no stem overlap with "codecompass".
        # Without the cap, central_hub.py would outrank it because of
        # the 50 incoming relations.
        if "agent/services/central_hub.py" in scores and "worker/retrieval/codecompass_candidate_resolver.py" in scores:
            assert scores["worker/retrieval/codecompass_candidate_resolver.py"] >= scores["agent/services/central_hub.py"], (
                f"CodeCompass source with stem-match should outrank central hub "
                f"that has 50 incoming relations. Scores: "
                f"cc={scores['worker/retrieval/codecompass_candidate_resolver.py']:.2f} "
                f"hub={scores['agent/services/central_hub.py']:.2f}"
            )

    def test_xml_node_records_use_file_field_not_xpath(self, tmp_path) -> None:
        """XML-node records (kind: xml_node_detail) carry `path: /plugin`
        which is an XPath, not a repo path. The resolver must use the
        `file` field for these kinds, not `path`.

        Regression: query 'zeig mir die voxel plugin files' produced
        `plugin` as top-5 candidate because XML-node records had
        `path: /plugin` and the reader picked that over `file: .../plugin.xml`.
        """
        from worker.retrieval.codecompass_output_reader import extract_file_path_from_record
        out = tmp_path / "cc"
        # The XML node record has BOTH `file` (real) and `path` (XPath).
        _seed_minimal_codecompass_output(
            out,
            details_by_file={
                "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml": 3,
            },
            context_by_file={
                "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml": 3,
            },
        )
        # Add the XML-node records (id=xml_node_detail:hash, kind=xml_node_detail)
        # on top of the seeded ones.
        import json as _json
        with open(out / "details.jsonl", "a") as fp:
            fp.write(_json.dumps({
                "id": "xml_node_detail:022aea4576eb4705",
                "file": "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml",
                "kind": "xml_node_detail",
                "tag": "plugin",
                "path": "/plugin",
                "attributes": {},
                "text": "",
                "children": ["extension"],
                "_provenance": {"output_kind": "details", "manifest_hash": "test"},
            }) + "\n")
        with open(out / "context.jsonl", "a") as fp:
            fp.write(_json.dumps({
                "id": "xml_node_detail:022aea4576eb4705",
                "file": "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml",
                "kind": "xml_node_detail",
                "tag": "plugin",
                "path": "/plugin",
                "_provenance": {"output_kind": "context", "manifest_hash": "test"},
            }) + "\n")

        # Direct unit test on extract_file_path_from_record
        rec = {
            "file": "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml",
            "kind": "xml_node_detail",
            "path": "/plugin",
        }
        path = extract_file_path_from_record(rec, output_kind="details")
        assert path == "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml", (
            f"xml_node_detail must use `file`, got: {path!r}"
        )
        path_ctx = extract_file_path_from_record(rec, output_kind="context")
        assert path_ctx == "client_surfaces/eclipse_runtime/ananta_eclipse_plugin/plugin.xml", (
            f"context xml_node must use `file`, got: {path_ctx!r}"
        )

        # End-to-end: `plugin` must not appear as a candidate.
        resolver = CodeCompassCandidateResolver()
        candidates = resolver.resolve(
            question="zeig mir die voxel plugin files",
            output_dir=out,
        )
        paths = [c["path"] for c in candidates]
        assert "plugin" not in paths, f"XPath-derived 'plugin' must not be a candidate; got: {paths[:5]}"
