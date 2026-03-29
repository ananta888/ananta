from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from rag_helper.application.generated_code import detect_generated_code
from rag_helper.application.importance_scoring import compute_importance_score
from rag_helper.application.incremental_cache import load_incremental_cache
from rag_helper.application.manifest_stats import (
    collect_error_entries,
    collect_extension_stats,
    collect_skip_reason_counts,
    count_records_by_kind,
)
from rag_helper.application.output_formats import build_context_records, build_embedding_records
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.project_processor import process_project

try:
    from rag_helper.extractors.xml_extractor import XmlExtractor
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    XmlExtractor = None


class _StubJavaExtractor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def pre_scan_types(self, rel_path: str, text: str) -> dict:
        return {"file": rel_path, "package": None, "imports": [], "type_names": []}

    def parse(self, rel_path: str, text: str, known_package_types: dict[str, set[str]]):
        return [{
            "kind": "java_file",
            "file": rel_path,
            "id": "java_file:1",
            "embedding_text": "java file",
        }], [{
            "kind": "java_method_detail",
            "file": rel_path,
            "id": "java_method_detail:1",
            "embedding_text": "detail",
            "code_snippet": "return 1;",
        }], [], {"kind": "java", "file": rel_path}


class _StubAdocExtractor:
    def parse(self, rel_path: str, text: str):
        return [], [], [], {"kind": "adoc", "file": rel_path}


class _StubXmlExtractor:
    parse_calls = 0

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def parse(self, rel_path: str, text: str):
        type(self).parse_calls += 1
        return [{"kind": "xml_file", "file": rel_path}], [{"kind": "xml_detail"}], [{"kind": "relation"}], {
            "kind": "xml",
            "file": rel_path,
        }


class _StubXsdExtractor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def parse(self, rel_path: str, text: str):
        return [], [], [], {"kind": "xsd", "file": rel_path}


class _ParallelXmlExtractor:
    parse_calls = 0
    thread_ids: set[int] = set()
    lock = threading.Lock()

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def parse(self, rel_path: str, text: str):
        time.sleep({
            "a.xml": 0.06,
            "b.xml": 0.03,
            "c.xml": 0.01,
        }.get(rel_path, 0.0))
        with type(self).lock:
            type(self).parse_calls += 1
            type(self).thread_ids.add(threading.get_ident())
        return [{
            "kind": "xml_file",
            "file": rel_path,
            "id": f"xml_file:{rel_path}",
            "embedding_text": rel_path,
        }], [], [], {"kind": "xml", "file": rel_path}


class _FailingXmlExtractor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def parse(self, rel_path: str, text: str):
        raise ValueError(f"boom:{rel_path}")


class _InterruptingXmlExtractor:
    parse_calls = 0

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def parse(self, rel_path: str, text: str):
        type(self).parse_calls += 1
        if rel_path == "b.xml":
            raise KeyboardInterrupt("stop-now")
        return [{"kind": "xml_file", "file": rel_path}], [], [], {"kind": "xml", "file": rel_path}


class _HeavyRelationXmlExtractor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def parse(self, rel_path: str, text: str):
        relations = [
            {"kind": "relation", "file": rel_path, "from": "xml_file:1", "to": f"xml_tag:{index}", "type": "contains_child_tag"}
            for index in range(10)
        ] + [
            {"kind": "relation", "file": rel_path, "from": "xml_file:1", "to": "xml_tag:important", "type": "extends"}
        ]
        return [{"kind": "xml_file", "file": rel_path, "id": "xml_file:1", "embedding_text": rel_path}], [], relations, {
            "kind": "xml",
            "file": rel_path,
        }


class ProcessingLimitsTests(unittest.TestCase):
    def setUp(self) -> None:
        _StubXmlExtractor.parse_calls = 0
        _ParallelXmlExtractor.parse_calls = 0
        _ParallelXmlExtractor.thread_ids = set()
        _InterruptingXmlExtractor.parse_calls = 0

    def test_process_project_skips_files_over_record_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "data.xml").write_text("<root><a /></root>", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(max_records_per_file=2),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["index_record_count"], 0)
            self.assertEqual(manifest["detail_record_count"], 0)
            self.assertEqual(manifest["relation_record_count"], 0)
            self.assertEqual(manifest["options"]["max_records_per_file"], 2)
            self.assertTrue(manifest["files"][0]["skipped"])
            self.assertEqual(manifest["files"][0]["skip_reason"], "max_records_per_file_exceeded")

    def test_process_project_passes_xml_mode_settings_to_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "config.xml").write_text("<beans />", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(
                    xml_mode="smart",
                    xml_repetitive_child_threshold=11,
                ),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["options"]["xml_mode"], "smart")
            self.assertEqual(manifest["options"]["xml_repetitive_child_threshold"], 11)

    def test_incremental_cache_reuses_unchanged_file_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            cache_file = Path(tmp_dir) / ".code_to_rag_cache.json"
            root.mkdir()
            (root / "config.xml").write_text("<beans />", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(),
                incremental=True,
                rebuild=False,
                cache_file=cache_file,
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )
            self.assertEqual(_StubXmlExtractor.parse_calls, 1)

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(),
                incremental=True,
                rebuild=False,
                cache_file=cache_file,
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(_StubXmlExtractor.parse_calls, 1)
            self.assertEqual(manifest["cache_hit_count"], 1)
            self.assertEqual(manifest["cache_miss_count"], 0)
            self.assertTrue(manifest["files"][0]["cache_hit"])

    def test_generated_code_mode_exclude_skips_matching_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir(parents=True)
            generated_dir = root / "target" / "generated-sources"
            generated_dir.mkdir(parents=True)
            (generated_dir / "User.java").write_text(
                "/* Generated by JAXB, do not edit */ class User {}",
                encoding="utf-8",
            )

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"java"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(generated_code_mode="exclude"),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["files"][0]["skipped"])
            self.assertEqual(manifest["files"][0]["skip_reason"], "generated_code_excluded")
            self.assertTrue(manifest["files"][0]["generated_code"])

    def test_generated_code_mode_mark_keeps_file_and_marks_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "User.java").write_text(
                "/* Generated by JAXB, do not edit */ class User {}",
                encoding="utf-8",
            )

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"java"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(generated_code_mode="mark"),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["files"][0].get("skipped", False))
            self.assertTrue(manifest["files"][0]["generated_code"])

    def test_process_project_parallelizes_misses_and_preserves_file_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            for name in ("a.xml", "b.xml", "c.xml"):
                (root / name).write_text("<root />", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(max_workers=3),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_ParallelXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            index_rows = [
                json.loads(line)
                for line in (out_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(_ParallelXmlExtractor.parse_calls, 3)
            self.assertGreaterEqual(len(_ParallelXmlExtractor.thread_ids), 2)
            self.assertEqual(manifest["effective_max_workers"], 3)
            self.assertEqual(manifest["options"]["max_workers"], 3)
            self.assertEqual([entry["file"] for entry in manifest["files"]], ["a.xml", "b.xml", "c.xml"])
            self.assertEqual([row["file"] for row in index_rows], ["a.xml", "b.xml", "c.xml"])

    def test_process_project_emits_progress_output_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "a.xml").write_text("<root />", encoding="utf-8")
            (root / "b.xml").write_text("<root />", encoding="utf-8")
            captured = StringIO()

            with redirect_stdout(captured):
                process_project(
                    root=root,
                    out_dir=out_dir,
                    extensions={"xml"},
                    excludes=set(),
                    include_code_snippets=False,
                    exclude_trivial_methods=False,
                    include_xml_node_details=True,
                    include_globs=[],
                    exclude_globs=[],
                    limits=ProcessingLimits(max_workers=1),
                    java_extractor_cls=_StubJavaExtractor,
                    adoc_extractor_cls=_StubAdocExtractor,
                    xml_extractor_cls=_StubXmlExtractor,
                    xsd_extractor_cls=_StubXsdExtractor,
                    show_progress=True,
                )

            output = captured.getvalue()
            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("[1/2  50%] a.xml", output)
            self.assertIn("[2/2 100%] b.xml", output)
            self.assertIn("cache_hits=0", output)
            self.assertTrue(manifest["options"]["show_progress"])

    def test_process_project_writes_error_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            error_log = Path(tmp_dir) / "errors.jsonl"
            root.mkdir()
            (root / "broken.xml").write_text("<root />", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_FailingXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
                error_log_file=error_log,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            error_rows = [
                json.loads(line)
                for line in error_log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(manifest["error_count"], 1)
            self.assertEqual(manifest["error_log_file"], str(error_log))
            self.assertEqual(manifest["options"]["error_log_file"], str(error_log))
            self.assertEqual(error_rows[0]["file"], "broken.xml")
            self.assertEqual(error_rows[0]["error"], "boom:broken.xml")

    def test_resume_reuses_checkpointed_results_after_interrupted_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            cache_file = Path(tmp_dir) / ".resume_cache.json"
            root.mkdir()
            (root / "a.xml").write_text("<root />", encoding="utf-8")
            (root / "b.xml").write_text("<root />", encoding="utf-8")

            with self.assertRaises(KeyboardInterrupt):
                process_project(
                    root=root,
                    out_dir=out_dir,
                    extensions={"xml"},
                    excludes=set(),
                    include_code_snippets=False,
                    exclude_trivial_methods=False,
                    include_xml_node_details=True,
                    include_globs=[],
                    exclude_globs=[],
                    limits=ProcessingLimits(max_workers=1),
                    java_extractor_cls=_StubJavaExtractor,
                    adoc_extractor_cls=_StubAdocExtractor,
                    xml_extractor_cls=_InterruptingXmlExtractor,
                    xsd_extractor_cls=_StubXsdExtractor,
                    resume=True,
                    cache_file=cache_file,
                )

            cache_data = load_incremental_cache(cache_file)
            self.assertIn("a.xml", cache_data["files"])
            self.assertNotIn("b.xml", cache_data["files"])
            self.assertTrue((Path(f"{cache_file}.d") / "xml.json").exists())

            _StubXmlExtractor.parse_calls = 0
            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(max_workers=1),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
                resume=True,
                cache_file=cache_file,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(_StubXmlExtractor.parse_calls, 1)
            self.assertEqual(manifest["cache_hit_count"], 1)
            self.assertEqual(manifest["cache_miss_count"], 1)
            self.assertTrue(manifest["resume_enabled"])
            self.assertTrue(manifest["files"][0]["cache_hit"])
            self.assertEqual(manifest["options"]["resume"], True)

    def test_process_project_writes_benchmark_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "config.xml").write_text("<beans />", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(benchmark_mode="basic"),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            benchmark = json.loads((out_dir / "benchmark.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["options"]["benchmark_mode"], "basic")
            self.assertIn("by_extension", benchmark)
            self.assertIn("xml", benchmark["by_extension"])
            self.assertGreaterEqual(benchmark["by_extension"]["xml"]["file_count"], 1)
            self.assertIn("slowest_files", benchmark)

    def test_process_project_dry_run_skips_output_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "config.xml").write_text("<beans />", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
                dry_run=True,
            )

            self.assertFalse((out_dir / "manifest.json").exists())
            self.assertFalse((out_dir / "index.jsonl").exists())

    def test_rebuild_ignores_existing_incremental_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            cache_file = Path(tmp_dir) / ".code_to_rag_cache.json"
            root.mkdir()
            (root / "config.xml").write_text("<beans />", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(),
                incremental=True,
                rebuild=False,
                cache_file=cache_file,
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(),
                incremental=True,
                rebuild=True,
                cache_file=cache_file,
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(_StubXmlExtractor.parse_calls, 2)
            self.assertTrue(manifest["cache_rebuilt"])

    def test_build_split_output_records(self) -> None:
        embedding_records = build_embedding_records([{
            "id": "java_file:1",
            "kind": "java_file",
            "file": "User.java",
            "embedding_text": "java file",
            "summary": {"type_count": 1},
            "role_labels": ["service"],
            "importance_score": 3.0,
        }])
        context_records = build_context_records([{
            "id": "java_method_detail:1",
            "kind": "java_method_detail",
            "file": "User.java",
            "embedding_text": "detail",
            "code_snippet": "return 1;",
        }])

        self.assertEqual(embedding_records[0]["embedding_text"], "java file")
        self.assertEqual(embedding_records[0]["importance_score"], 3.0)
        self.assertNotIn("embedding_text", context_records[0])
        self.assertEqual(context_records[0]["code_snippet"], "return 1;")

    def test_build_compact_context_records_drops_bulky_fields(self) -> None:
        context_records = build_context_records([{
            "id": "java_method_detail:1",
            "kind": "java_method_detail",
            "file": "User.java",
            "embedding_text": "detail",
            "code_snippet": "return 1;",
            "text": "x" * 500,
            "calls": [f"call{i}" for i in range(20)],
            "fields": [{"name": "field", "description": "d" * 400}],
        }], mode="compact")

        record = context_records[0]
        self.assertNotIn("embedding_text", record)
        self.assertNotIn("code_snippet", record)
        self.assertEqual(len(record["text"]), 200)
        self.assertEqual(len(record["calls"]), 12)
        self.assertEqual(record["fields"][0]["description"], "d" * 160)

    def test_process_project_writes_split_outputs_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "User.java").write_text("class User {}", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"java"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(retrieval_output_mode="both"),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            embedding_lines = (out_dir / "embedding.jsonl").read_text(encoding="utf-8").strip().splitlines()
            context_lines = (out_dir / "context.jsonl").read_text(encoding="utf-8").strip().splitlines()

            self.assertEqual(manifest["embedding_record_count"], 2)
            self.assertEqual(manifest["context_record_count"], 1)
            self.assertEqual(len(embedding_lines), 2)
            self.assertEqual(len(context_lines), 1)

    def test_process_project_writes_partitioned_outputs_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "config.xml").write_text("<beans />", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(
                    relation_output_mode="split",
                    output_partition_mode="by-kind",
                ),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertFalse((out_dir / "relations.jsonl").exists())
            self.assertTrue((out_dir / "relations_by_type" / "unknown.jsonl").exists())
            self.assertTrue((out_dir / "index_by_kind" / "xml_file.jsonl").exists())
            self.assertTrue((out_dir / "details_by_kind" / "xml_detail.jsonl").exists())
            self.assertEqual(manifest["options"]["relation_output_mode"], "split")
            self.assertEqual(manifest["options"]["output_partition_mode"], "by-kind")
            self.assertTrue(manifest["partitioned_outputs"]["relations"])

    def test_process_project_prunes_relations_by_priority_when_limit_is_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "config.xml").write_text("<beans />", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=False,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(max_relation_records_per_file=3),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_HeavyRelationXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            relation_rows = [
                json.loads(line)
                for line in (out_dir / "relations.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(len(relation_rows), 3)
            self.assertTrue(any((row.get("relation") or row.get("type")) == "extends" for row in relation_rows))
            self.assertEqual(manifest["files"][0]["relation_compaction"]["kept_relation_count"], 3)

    def test_importance_score_prefers_service_and_architecture_records(self) -> None:
        service_score = compute_importance_score({
            "kind": "java_type",
            "role_labels": ["service"],
            "name": "UserService",
        })
        adoc_score = compute_importance_score({
            "kind": "adoc_section",
            "title": "Architecture Overview",
            "section_path": ["Architecture", "Overview"],
        })
        generated_score = compute_importance_score({
            "kind": "java_type",
            "role_labels": ["service"],
            "name": "GeneratedService",
            "generated_code": True,
        })

        self.assertGreater(service_score, 2.5)
        self.assertGreater(adoc_score, 3.0)
        self.assertLess(generated_score, service_score)

    def test_manifest_stat_helpers_aggregate_counts(self) -> None:
        kind_counts = count_records_by_kind(
            [{"kind": "java_file"}, {"kind": "java_type"}],
            [{"kind": "java_method_detail"}],
            [{"kind": "relation"}, {"kind": "relation"}],
        )
        skip_counts = collect_skip_reason_counts([
            {"skipped": True, "skip_reason": "generated_code_excluded"},
            {"skipped": True, "skip_reason": "generated_code_excluded"},
            {"skipped": True, "skip_reason": "xml_mode_filtered"},
        ])
        errors = collect_error_entries([
            {"file": "A.java", "ext": "java", "stage": "parse", "error": "boom"},
            {"file": "B.xml", "ext": "xml"},
        ])
        extension_stats = collect_extension_stats([
            {"ext": "java", "cache_hit": True},
            {"ext": "java", "skipped": True},
            {"ext": "xml", "error": "bad xml"},
        ])

        self.assertEqual(kind_counts["relation"], 2)
        self.assertEqual(skip_counts["generated_code_excluded"], 2)
        self.assertEqual(errors[0]["file"], "A.java")
        self.assertEqual(extension_stats["java"]["file_count"], 2)
        self.assertEqual(extension_stats["java"]["cache_hit_count"], 1)
        self.assertEqual(extension_stats["xml"]["error_count"], 1)

    def test_process_project_manifest_contains_richer_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "User.java").write_text("class User {}", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"java"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=True,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(manifest["record_counts_by_kind"]["java_file"], 1)
            self.assertEqual(manifest["record_counts_by_kind"]["java_method_detail"], 1)
            self.assertEqual(manifest["error_count"], 0)
            self.assertEqual(manifest["extension_stats"]["java"]["file_count"], 1)
            self.assertEqual(manifest["skip_reason_counts"], {})

    @unittest.skipUnless(XmlExtractor is not None, "lxml dependency missing")
    def test_xml_extractor_rejects_documents_over_node_limit(self) -> None:
        extractor = XmlExtractor(max_xml_nodes=2)

        with self.assertRaisesRegex(ValueError, "max_xml_nodes_exceeded"):
            extractor.parse("config.xml", "<root><a /><b /></root>")

    @unittest.skipUnless(XmlExtractor is not None, "lxml dependency missing")
    def test_xml_extractor_config_only_skips_non_config_xml(self) -> None:
        extractor = XmlExtractor(xml_mode="config-only")

        with self.assertRaisesRegex(Exception, "xml_mode_filtered"):
            extractor.parse("data/export.xml", "<rows><row id='1'/><row id='2'/></rows>")

    @unittest.skipUnless(XmlExtractor is not None, "lxml dependency missing")
    def test_xml_extractor_smart_accepts_mapper_xml(self) -> None:
        extractor = XmlExtractor(xml_mode="smart")

        index_records, _, _, stats = extractor.parse(
            "config/user-mapper.xml",
            "<mapper namespace='demo.UserMapper'><select id='findAll'/></mapper>",
        )

        self.assertEqual(index_records[0]["xml_kind"], "config")
        self.assertEqual(stats["xml_kind"], "config")

    @unittest.skipUnless(XmlExtractor is not None, "lxml dependency missing")
    def test_xml_extractor_smart_skips_repetitive_data_xml(self) -> None:
        extractor = XmlExtractor(xml_mode="smart", repetitive_child_threshold=4)

        with self.assertRaisesRegex(Exception, "xml_mode_filtered"):
            extractor.parse(
                "data/export.xml",
                "<rows><row id='1'/><row id='2'/><row id='3'/><row id='4'/><row id='5'/></rows>",
            )

    @unittest.skipUnless(XmlExtractor is not None, "lxml dependency missing")
    def test_xml_extractor_by_tag_relation_mode_aggregates_child_relations(self) -> None:
        extractor = XmlExtractor(relation_mode="by-tag")

        _, _, relation_records, _ = extractor.parse(
            "config.xml",
            "<root><entry><value /></entry><entry><value /></entry></root>",
        )

        child_relations = [record for record in relation_records if record["relation"] == "contains_child_tag"]
        self.assertEqual(len(child_relations), 2)
        self.assertTrue(all(record["source_kind"] == "xml_tag" for record in child_relations))

    @unittest.skipUnless(XmlExtractor is not None, "lxml dependency missing")
    def test_xml_extractor_summary_relation_mode_skips_child_relations(self) -> None:
        extractor = XmlExtractor(index_mode="summary", relation_mode="summary")

        index_records, _, relation_records, _ = extractor.parse(
            "config.xml",
            "<root><entry><value /></entry><entry><value /></entry></root>",
        )

        self.assertTrue(any(record["kind"] == "xml_tag_summary" for record in index_records))
        child_relations = [record for record in relation_records if record["relation"] == "contains_child_tag"]
        self.assertEqual(child_relations, [])

    @unittest.skipUnless(XmlExtractor is not None, "lxml dependency missing")
    def test_xml_extractor_summary_index_mode_writes_aggregated_tag_record(self) -> None:
        extractor = XmlExtractor(index_mode="summary")

        index_records, _, _, _ = extractor.parse(
            "config.xml",
            "<root><entry id='1'><value /></entry><entry id='2'><value /></entry></root>",
        )

        kinds = {record["kind"] for record in index_records}
        self.assertIn("xml_file", kinds)
        self.assertIn("xml_tag_summary", kinds)
        self.assertNotIn("xml_tag", kinds)


if __name__ == "__main__":
    unittest.main()
