from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rag_helper.application.generated_code import detect_generated_code
from rag_helper.application.importance_scoring import compute_importance_score
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


class ProcessingLimitsTests(unittest.TestCase):
    def setUp(self) -> None:
        _StubXmlExtractor.parse_calls = 0

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
            self.assertEqual(manifest["options"]["generated_code_mode"], "mark")

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

            self.assertEqual(manifest["embedding_record_count"], 1)
            self.assertEqual(manifest["context_record_count"], 1)
            self.assertEqual(len(embedding_lines), 1)
            self.assertEqual(len(context_lines), 1)

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


if __name__ == "__main__":
    unittest.main()
