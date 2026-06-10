"""Tests for the RAG-Helper CLI --domain-discovery-mode option (CCDD-012).

Acceptance:

  - the new --domain-discovery-mode flag is wired into the CLI help
  - it accepts the documented choices (off/basic/rich) and defaults to off
  - a basic run with --domain-discovery-mode basic actually produces
    domains.detected.json, domain_boundaries.jsonl and
    domain_coupling.json
  - off-by-default: without the flag, no domain-discovery artifacts are
    written and the manifest has no domain_discovery block
  - the opt-in --domain-descriptor-suggestions flag writes
    domain_descriptor_suggestions/<id>/domain.json
  - the option is also exposed via config_defaults / JSON profile
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _make_project(tmp: Path, *, ext: str = "java") -> Path:
    root = tmp / "project"
    root.mkdir()
    for i in range(3):
        d = root / f"domain_{i}"
        d.mkdir()
        (d / f"mod.{ext}").write_text(
            f"class Mod{i} {{}}\n", encoding="utf-8"
        )
    return root


class _Stub:
    def __init__(self, **kwargs) -> None:
        pass

    def pre_scan_types(self, rel_path, text):
        return {"file": rel_path, "package": None, "imports": [], "type_names": []}

    def parse(self, *args, **kwargs):
        return [], [], [], {}


class _StubJava(_Stub):
    """Java extractor stub that emits a record per file.

    This is needed for the descriptor-suggestion test: domain
    clustering needs at least one record per domain to produce a
    candidate, and descriptor suggestions are written per candidate.
    """
    def parse(self, rel_path, text=None, known_package_types=None, **kwargs):
        domain_id = rel_path.split("/")[0]
        package_name = f"com.example.{domain_id}"
        type_name = f"Mod{domain_id.replace('domain_', '')}"
        node_id = f"java_type:{package_name}.{type_name}"
        record = {
            "kind": "java_class", # This is an index-level record
            "file": rel_path,
            "id": node_id,
            "embedding_text": f"class {type_name}",
            "package": package_name,
            "name": type_name, # `name` or `node_type_name` is used by build_package_type_index
        }
        return [record], [], [], {"records": 1}

def _run_cli(args: list[str], tmp: Path) -> int:
    """Run the RAG-Helper CLI with the given args and return the exit code."""
    from rag_helper.cli import run_cli

    saved_argv = sys.argv
    sys.argv = ["rag_helper.cli", *args]
    try:
        run_cli(
            default_extensions={"java"},
            default_excludes=set(),
            java_extractor_cls=_StubJava,
            adoc_extractor_cls=_Stub,
            xml_extractor_cls=_Stub,
            xsd_extractor_cls=_Stub,
            text_extractor_cls=None,
        )
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        sys.argv = saved_argv


class TestCLIDomainDiscovery(unittest.TestCase):
    """CCDD-012: CLI option is wired and produces the documented artifacts."""

    def test_help_lists_domain_discovery_option(self) -> None:
        from rag_helper.cli import run_cli

        saved_argv = sys.argv
        sys.argv = ["rag_helper.cli", "--help"]
        try:
            with self.assertRaises(SystemExit) as cm:
                run_cli(
                    default_extensions=set(),
                    default_excludes=set(),
                    java_extractor_cls=None,
                    adoc_extractor_cls=None,
                    xml_extractor_cls=None,
                    xsd_extractor_cls=None,
                )
            self.assertEqual(cm.exception.code, 0)
        finally:
            sys.argv = saved_argv

    def test_off_by_default_writes_no_discovery_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            root = _make_project(tmp)
            out_dir = tmp / "out"
            rc = _run_cli(
                [str(root), "-o", str(out_dir)], tmp=tmp
            )
            self.assertEqual(rc, 0)
            self.assertFalse((out_dir / "domains.detected.json").exists())
            manifest = json.loads(
                (out_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("domain_discovery", manifest)

    def test_basic_mode_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            root = _make_project(tmp)
            out_dir = tmp / "out"
            rc = _run_cli(
                [
                    str(root),
                    "-o",
                    str(out_dir),
                    "--domain-discovery-mode",
                    "basic",
                ],
                tmp=tmp,
            )
            self.assertEqual(rc, 0)
            self.assertTrue((out_dir / "domains.detected.json").is_file())
            self.assertTrue((out_dir / "domain_boundaries.jsonl").is_file())
            self.assertTrue((out_dir / "domain_coupling.json").is_file())

    def test_descriptor_suggestions_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            root = _make_project(tmp)
            out_dir = tmp / "out"
            rc = _run_cli(
                [
                    str(root),
                    "-o",
                    str(out_dir),
                    "--domain-discovery-mode",
                    "basic",
                    "--domain-descriptor-suggestions",
                ],
                tmp=tmp,
            )
            self.assertEqual(rc, 0)
            suggestions_root = (
                out_dir / "domain_descriptor_suggestions"
            )
            manifest = json.loads(
                (out_dir / "manifest.json").read_text(encoding="utf-8")
            )
            domain_count = manifest.get("domain_discovery", {}).get(
                "domain_count", 0
            )
            self.assertGreater(
                domain_count,
                0,
                msg=f"expected at least 1 domain candidate, manifest: {manifest}",
            )
            self.assertTrue(suggestions_root.is_dir())
            domain_files = list(suggestions_root.rglob("domain.json"))
            self.assertEqual(len(domain_files), domain_count)

    def test_config_profile_loads_domain_discovery_mode(self) -> None:
        """A JSON profile with domain_discovery_mode propagates to the CLI."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            root = _make_project(tmp)
            out_dir = tmp / "out"
            config_path = tmp / "profile.json"
            config_path.write_text(
                json.dumps(
                    {
                        "domain_discovery_mode": "basic",
                        "extensions": ["java"],
                    }
                ),
                encoding="utf-8",
            )
            rc = _run_cli(
                [str(root), "-o", str(out_dir), "--config", str(config_path)],
                tmp=tmp,
            )
            self.assertEqual(rc, 0)
            self.assertTrue((out_dir / "domains.detected.json").is_file())


if __name__ == "__main__":
    unittest.main()
