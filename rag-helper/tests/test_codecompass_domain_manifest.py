"""Tests for CCDD-014: manifest domain-discovery statistics.

Acceptance:

  - manifest.json contains the domain_discovery block at basic/rich
  - manifest.json has no domain_discovery block when mode is off
  - compact manifest_output_mode only keeps the summary numbers
  - off-by-default: no discovery artifacts are written when mode is off
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.project_processor import process_project


class _StubJavaExtractor:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def pre_scan_types(self, rel_path: str, text: str) -> dict:
        return {"file": rel_path, "package": None, "imports": [], "type_names": []}

    def parse(self, rel_path: str, text: str, known_package_types: dict[str, set[str]]):
        return [
            {
                "kind": "java_file",
                "file": rel_path,
                "id": f"java_file:{rel_path}",
                "embedding_text": "java file",
            }
        ], [], [], {}


class _StubAdocExtractor:
    def __init__(self, **kwargs) -> None:
        pass

    def parse(self, rel_path: str, text: str):
        return [], [], [], {}


class _StubXmlExtractor:
    def __init__(self, **kwargs) -> None:
        pass

    def parse(self, rel_path: str, text: str):
        return [], [], [], {}


class _StubXsdExtractor:
    def __init__(self, **kwargs) -> None:
        pass

    def parse(self, rel_path: str, text: str):
        return [], [], [], {}


def _make_project(tmp: Path, *, file_count: int = 4) -> tuple[Path, Path]:
    root = tmp / "project"
    out_dir = tmp / "out"
    root.mkdir()
    for i in range(file_count):
        domain_dir = root / f"domain_{i}"
        domain_dir.mkdir()
        (domain_dir / "mod.py").write_text(
            f"def hello_{i}():\n    return 'hi from domain {i}'\n",
            encoding="utf-8",
        )
    return root, out_dir


class TestDomainDiscoveryManifest(unittest.TestCase):
    """CCDD-014: manifest carries the domain_discovery block at basic/rich."""

    def test_off_mode_omits_discovery_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            root, out_dir = _make_project(tmp)
            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"py"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=False,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(domain_discovery_mode="off"),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
                text_extractor_cls=None,
            )
            manifest = json.loads(
                (out_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("domain_discovery", manifest)
            self.assertFalse((out_dir / "domains.detected.json").exists())
            self.assertFalse((out_dir / "domain_boundaries.jsonl").exists())
            self.assertFalse((out_dir / "domain_coupling.json").exists())

    def test_basic_mode_writes_artifacts_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            root, out_dir = _make_project(tmp, file_count=3)
            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"py"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=False,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(domain_discovery_mode="basic"),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
                text_extractor_cls=None,
            )
            manifest = json.loads(
                (out_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertIn("domain_discovery", manifest)
            block = manifest["domain_discovery"]
            self.assertEqual(block["mode"], "basic")
            for required in (
                "domain_count",
                "unassigned_record_count",
                "boundary_warning_count",
                "output_files",
            ):
                self.assertIn(required, block)
            self.assertNotIn("domains", block)
            self.assertTrue((out_dir / "domains.detected.json").is_file())
            self.assertTrue((out_dir / "domain_boundaries.jsonl").is_file())
            self.assertTrue((out_dir / "domain_coupling.json").is_file())
            self.assertIn(
                "domains.detected.json",
                [Path(p).name for p in block["output_files"]],
            )

    def test_rich_mode_embeds_domains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            root, out_dir = _make_project(tmp, file_count=3)
            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"py"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=False,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(domain_discovery_mode="rich"),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
                text_extractor_cls=None,
            )
            manifest = json.loads(
                (out_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertIn("domain_discovery", manifest)
            self.assertEqual(manifest["domain_discovery"]["mode"], "rich")
            self.assertIn("domains", manifest["domain_discovery"])

    def test_compact_manifest_keeps_summary_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            root, out_dir = _make_project(tmp, file_count=3)
            process_project(
                root=root,
                out_dir=out_dir,
                extensions={"py"},
                excludes=set(),
                include_code_snippets=False,
                exclude_trivial_methods=False,
                include_xml_node_details=False,
                include_globs=[],
                exclude_globs=[],
                limits=ProcessingLimits(
                    domain_discovery_mode="rich",
                    manifest_output_mode="compact",
                ),
                java_extractor_cls=_StubJavaExtractor,
                adoc_extractor_cls=_StubAdocExtractor,
                xml_extractor_cls=_StubXmlExtractor,
                xsd_extractor_cls=_StubXsdExtractor,
                text_extractor_cls=None,
            )
            manifest = json.loads(
                (out_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertIn("domain_discovery", manifest)
            block = manifest["domain_discovery"]
            self.assertNotIn("domains", block)
            self.assertNotIn("warnings", block)
            for required in (
                "mode",
                "domain_count",
                "unassigned_record_count",
                "boundary_warning_count",
                "output_files",
            ):
                self.assertIn(required, block)


if __name__ == "__main__":
    unittest.main()
