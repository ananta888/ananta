from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

try:
    from tree_sitter import Language, Parser
    import tree_sitter_c_sharp as tscsharp
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    Language = None
    Parser = None
    tscsharp = None

from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.project_processor import process_project
from rag_helper.extractors.csharp_ast_helpers import (
    extract_xml_documentation,
    extract_xml_documentation_summary,
)
from rag_helper.extractors.csharp_type_extractor import CSharpTypeContext, extract_type


class _StubCSharpExtractor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def pre_scan_types(self, rel_path: str, text: str) -> dict:
        return {"file": rel_path, "namespace": "Demo.App", "usings": ["System"], "type_names": ["UserService"]}

    def parse(self, rel_path: str, text: str, known_namespace_types: dict[str, set[str]]):
        return [{
            "kind": "cs_file",
            "file": rel_path,
            "id": "cs_file:1",
            "namespace": "Demo.App",
            "usings": ["System"],
            "types": [{"name": "UserService", "type_kind": "class", "property_count": 1, "method_count": 1, "constructor_count": 1, "field_count": 1, "role_labels": ["service"]}],
            "embedding_text": "cs file",
            "summary": {"using_count": 1, "type_count": 1, "property_count": 1, "method_count": 1, "constructor_count": 1},
        }, {
            "kind": "cs_type",
            "file": rel_path,
            "id": "cs_type:1",
            "parent_id": "cs_file:1",
            "namespace": "Demo.App",
            "usings": ["System"],
            "name": "UserService",
            "type_kind": "class",
            "modifiers": ["public"],
            "attributes": [],
            "documentation": "Service docs.",
            "documentation_summary": "Service docs.",
            "extends": None,
            "extends_resolved": [],
            "implements": [],
            "implements_resolved": [],
            "fields": [{"name": "_repo", "type": "IRepo", "resolved_types": ["Demo.App.IRepo"], "attributes": []}],
            "properties": ["Name: string"],
            "methods": ["RunAsync(User user): Task<User>"],
            "constructors": ["UserService(IRepo repo)"],
            "used_types": ["Demo.App.IRepo"],
            "called_methods": ["repo.Save(user)"],
            "role_labels": ["service"],
            "roles": {"role_labels": ["service"]},
            "type_resolution_conflicts": [],
            "embedding_text": "cs type",
            "summary": "class UserService",
        }], [{
            "kind": "cs_property",
            "file": rel_path,
            "id": "cs_property:1",
            "parent_id": "cs_type:1",
            "class": "UserService",
            "name": "Name",
            "property_type": "string",
            "resolved_property_types": ["string"],
            "modifiers": ["public"],
            "attributes": [],
            "documentation": "Name docs.",
            "documentation_summary": "Name docs.",
            "accessors": ["get", "set"],
            "is_auto_property": True,
            "is_trivial": True,
            "embedding_text": "property",
        }], [{
            "kind": "relation",
            "file": rel_path,
            "id": "relation:1",
            "source_id": "cs_file:1",
            "source_kind": "cs_file",
            "source_name": rel_path,
            "relation": "contains_type",
            "target": "UserService",
            "target_resolved": "cs_type:1",
            "weight": 1,
            "from": "cs_file:1",
            "to": "cs_type:1",
            "type": "contains_type",
        }], {"kind": "cs", "file": rel_path, "namespace": "Demo.App"}


class _NoopExtractor:
    def parse(self, rel_path: str, text: str):
        return [], [], [], {"kind": "noop", "file": rel_path}


class CSharpDocumentationHelperTests(unittest.TestCase):
    def test_extract_xml_documentation_normalizes_summary_and_param_tags(self) -> None:
        src = (
            b"/// <summary>Saves a user.</summary>\n"
            b"/// <param name=\"id\">User id.</param>\n"
            b"void Save() {}"
        )
        node = SimpleNamespace(start_point=(2, 0))

        documentation = extract_xml_documentation(node, src)

        self.assertEqual(documentation, "Saves a user.\n\n@param id User id.")
        self.assertEqual(extract_xml_documentation_summary(documentation), "Saves a user.")


def _parse_first_type(code: str):
    parser = Parser()
    parser.language = Language(tscsharp.language())
    src = code.encode("utf-8")
    root = parser.parse(src).root_node
    type_node = next(child for child in root.children if child.type == "class_declaration")
    return src, type_node


@unittest.skipUnless(Language is not None and Parser is not None and tscsharp is not None, "tree_sitter C# dependencies missing")
class CSharpTypeExtractorTests(unittest.TestCase):
    def test_extract_type_includes_properties_methods_and_documentation(self) -> None:
        src, type_node = _parse_first_type(
            """
            using System;
            using System.Threading.Tasks;
            namespace Demo.App;
            /// <summary>User service docs.</summary>
            public class UserService : BaseService, IDisposable
            {
                private readonly IRepo repo;

                /// <summary>Name docs.</summary>
                public string Name { get; set; }

                /// <summary>Create service.</summary>
                public UserService(IRepo repo) { this.repo = repo; }

                /// <summary>Runs work.</summary>
                public async Task<User> RunAsync(User user) { return repo.Save(user); }
            }
            """
        )
        ctx = CSharpTypeContext(
            rel_path="UserService.cs",
            src=src,
            namespace_name="Demo.App",
            usings=["System", "System.Threading.Tasks"],
            using_map={"IDisposable": "System.IDisposable", "Task": "System.Threading.Tasks.Task"},
            using_namespaces=["System", "System.Threading.Tasks"],
            known_namespace_types={"Demo.App": {"UserService", "BaseService", "IRepo", "User"}},
            same_file_types={"UserService"},
            include_code_snippets=False,
            exclude_trivial_methods=False,
            max_methods_per_class=None,
            detail_mode="full",
            relation_mode="full",
            mark_import_conflicts=True,
            resolve_method_targets=True,
            embedding_text_mode="verbose",
        )

        type_record, detail_records, relation_records, stats = extract_type(ctx, type_node)

        method_record = next(record for record in detail_records if record["kind"] == "cs_method")
        property_record = next(record for record in detail_records if record["kind"] == "cs_property")
        constructor_record = next(record for record in detail_records if record["kind"] == "cs_constructor")

        self.assertEqual(type_record["kind"], "cs_type")
        self.assertEqual(type_record["documentation_summary"], "User service docs.")
        self.assertEqual(type_record["properties"], ["Name: string"])
        self.assertIn("service", type_record["role_labels"])
        self.assertEqual(property_record["documentation_summary"], "Name docs.")
        self.assertTrue(property_record["is_auto_property"])
        self.assertEqual(constructor_record["documentation_summary"], "Create service.")
        self.assertEqual(method_record["documentation_summary"], "Runs work.")
        self.assertTrue(any(target["target_resolved"] == "Demo.App.IRepo.Save" for target in method_record["resolved_call_targets"]))
        self.assertTrue(any(rel["relation"] == "declares_property" for rel in relation_records))
        self.assertEqual(stats["property_count"], 1)


class CSharpProcessingIntegrationTests(unittest.TestCase):
    def test_process_project_writes_csharp_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "project"
            out_dir = Path(tmp_dir) / "out"
            root.mkdir()
            (root / "UserService.cs").write_text("class UserService {}", encoding="utf-8")

            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"cs"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=False,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(retrieval_output_mode="split"),
                java_extractor_cls=_StubCSharpExtractor,
                csharp_extractor_cls=_StubCSharpExtractor,
                adoc_extractor_cls=_NoopExtractor,
                xml_extractor_cls=_NoopExtractor,
                xsd_extractor_cls=_NoopExtractor,
            )

            manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
            index_rows = [
                json.loads(line)
                for line in (out_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            kinds = {row["kind"] for row in index_rows}
            self.assertIn("cs_file", kinds)
            self.assertIn("cs_type", kinds)
            self.assertIn("csharp_namespace_summary", kinds)
            self.assertEqual(manifest["summary_records"]["csharp_namespace_summary_count"], 1)


if __name__ == "__main__":
    unittest.main()
