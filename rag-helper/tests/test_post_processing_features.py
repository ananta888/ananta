from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.project_processor import process_project


class _DuplicateJavaExtractor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def pre_scan_types(self, rel_path: str, text: str) -> dict:
        return {"file": rel_path, "package": None, "imports": [], "type_names": ["Demo"]}

    def parse(self, rel_path: str, text: str, known_package_types: dict[str, set[str]]):
        type_name = Path(rel_path).stem
        return [{
            "kind": "java_type",
            "file": rel_path,
            "id": f"java_type:{type_name}",
            "name": type_name,
            "type_kind": "class",
            "fields": [
                {"name": "id", "type": "Long", "annotations": []},
                {"name": "name", "type": "String", "annotations": []},
            ],
            "role_labels": ["dto"],
            "annotations": [],
            "embedding_text": "dto",
        }], [], [], {"kind": "java", "file": rel_path}


class _JpaJavaExtractor(_DuplicateJavaExtractor):
    def parse(self, rel_path: str, text: str, known_package_types: dict[str, set[str]]):
        return [{
            "kind": "java_type",
            "file": rel_path,
            "id": "java_type:User",
            "name": "User",
            "type_kind": "class",
            "fields": [
                {"name": "id", "type": "Long", "annotations": []},
                {"name": "roles", "type": "List<Role>", "annotations": ["@OneToMany"]},
            ],
            "role_labels": ["entity"],
            "annotations": ["@Entity"],
            "embedding_text": "entity",
        }], [], [], {"kind": "java", "file": rel_path}


class _PomXmlExtractor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def parse(self, rel_path: str, text: str):
        return [{
            "kind": "xml_file",
            "file": rel_path,
            "id": f"xml_file:{rel_path}",
            "xml_kind": "config",
            "embedding_text": "pom",
        }], [
            {"kind": "xml_node_detail", "file": rel_path, "id": "groupId", "tag": "groupId", "path": "/project/groupId", "text": "com.example", "attributes": {}},
            {"kind": "xml_node_detail", "file": rel_path, "id": "artifactId", "tag": "artifactId", "path": "/project/artifactId", "text": "demo", "attributes": {}},
            {"kind": "xml_node_detail", "file": rel_path, "id": "version", "tag": "version", "path": "/project/version", "text": "1.0.0", "attributes": {}},
            {"kind": "xml_node_detail", "file": rel_path, "id": "depGroup", "tag": "groupId", "path": "/project/dependencies/dependency/groupId", "text": "org.example", "attributes": {}},
            {"kind": "xml_node_detail", "file": rel_path, "id": "depArtifact", "tag": "artifactId", "path": "/project/dependencies/dependency/artifactId", "text": "lib", "attributes": {}},
            {"kind": "xml_node_detail", "file": rel_path, "id": "depVersion", "tag": "version", "path": "/project/dependencies/dependency/version", "text": "2.0.0", "attributes": {}},
        ], [], {"kind": "xml", "file": rel_path}


class _NoopExtractor:
    def parse(self, rel_path: str, text: str):
        return [], [], [], {"kind": "noop", "file": rel_path}


class _PythonTsExtractor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def parse(self, rel_path: str, text: str):
        if rel_path.endswith(".py"):
            return [{
                "kind": "python_file",
                "file": rel_path,
                "id": f"python_file:{rel_path}",
                "imports": ["os"],
                "classes": [{"name": "Worker", "methods": [{"name": "run"}]}],
                "functions": [{"name": "helper"}],
                "symbols": [{"kind": "class", "name": "Worker", "line": 1}],
                "embedding_text": "python",
                "summary": {
                    "import_count": 1,
                    "class_count": 1,
                    "function_count": 1,
                    "method_count": 1,
                    "symbol_count": 1,
                },
            }], [], [], {"kind": "python", "file": rel_path}
        return [{
            "kind": "typescript_file",
            "file": rel_path,
            "id": f"typescript_file:{rel_path}",
            "imports": ["./base"],
            "symbols": [{"kind": "class", "name": "UiService", "line": 1}],
            "embedding_text": "typescript",
            "summary": {
                "import_count": 1,
                "class_count": 1,
                "function_count": 0,
                "method_count": 2,
                "symbol_count": 1,
            },
        }], [], [], {"kind": "typescript", "file": rel_path}


class PostProcessingFeatureTests(unittest.TestCase):
    def test_duplicate_detection_writes_report_and_relations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "A.java").write_text("class A {}", encoding="utf-8")
            (root / "B.java").write_text("class B {}", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"java"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=False,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(duplicate_detection_mode="basic"),
                java_extractor_cls=_DuplicateJavaExtractor,
                adoc_extractor_cls=_NoopExtractor,
                xml_extractor_cls=_NoopExtractor,
                xsd_extractor_cls=_NoopExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            duplicates = json.loads((out_dir / "duplicates.json").read_text(encoding="utf-8"))
            relations = [
                json.loads(line)
                for line in (out_dir / "relations.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(manifest["options"]["duplicate_detection_mode"], "basic")
            self.assertEqual(duplicates["group_count"], 1)
            self.assertTrue(any(item.get("relation") == "duplicate_candidate" for item in relations))

    def test_specialized_chunkers_add_domain_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "pom.xml").write_text("<project />", encoding="utf-8")
            (root / "User.java").write_text("class User {}", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"xml", "java"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=False,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(specialized_chunker_mode="basic"),
                java_extractor_cls=_JpaJavaExtractor,
                adoc_extractor_cls=_NoopExtractor,
                xml_extractor_cls=_PomXmlExtractor,
                xsd_extractor_cls=_NoopExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            details = [
                json.loads(line)
                for line in (out_dir / "details.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            detail_kinds = {item["kind"] for item in details}
            self.assertEqual(manifest["options"]["specialized_chunker_mode"], "basic")
            self.assertIn("maven_pom_chunk", detail_kinds)
            self.assertIn("maven_dependency_chunk", detail_kinds)
            self.assertIn("jpa_entity_chunk", detail_kinds)
            self.assertGreaterEqual(manifest["specialized_chunks"]["maven_pom_chunk_count"], 1)

    def test_builds_summary_records_and_output_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            (root / "pkg").mkdir(parents=True)
            (root / "web").mkdir(parents=True)
            (root / "pkg" / "service.py").write_text("class Worker: pass", encoding="utf-8")
            (root / "web" / "service.ts").write_text("export class UiService {}", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"py", "ts"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=False,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(output_bundle_mode="zip"),
                java_extractor_cls=_DuplicateJavaExtractor,
                adoc_extractor_cls=_NoopExtractor,
                xml_extractor_cls=_NoopExtractor,
                xsd_extractor_cls=_NoopExtractor,
                text_extractor_cls=_PythonTsExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            index_records = [
                json.loads(line)
                for line in (out_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            kinds = {item["kind"] for item in index_records}
            self.assertIn("python_module_summary", kinds)
            self.assertIn("typescript_folder_summary", kinds)
            self.assertEqual(manifest["summary_records"]["summary_record_count"], 2)
            self.assertEqual(manifest["output_bundle"]["mode"], "zip")
            self.assertTrue((out_dir / "output_bundle.zip").exists())
