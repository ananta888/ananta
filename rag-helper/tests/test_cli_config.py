from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rag_helper.application.config_profiles import load_profile_config
from rag_helper.cli import run_cli


class CliConfigTests(unittest.TestCase):
    def test_run_cli_loads_json_profile_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            project_dir = base / "project"
            profile_dir = base / "profiles"
            project_dir.mkdir()
            profile_dir.mkdir()
            config_path = profile_dir / "rag-profile.json"
            config_path.write_text(json.dumps({
                "root": "../project",
                "out": "../out",
                "extensions": ["xml", "java"],
                "filters": {
                    "include_glob": ["src/**/*.java"],
                    "exclude_glob": ["target/**"],
                },
                "limits": {
                    "max_workers": 3,
                    "max_records_per_file": 25,
                },
                "modes": {
                    "xml_mode": "smart",
                },
                "flags": {
                    "progress": True,
                    "resume": True,
                },
            }), encoding="utf-8")

            captured: dict = {}

            with patch("sys.argv", ["rag-helper", "--config", str(config_path)]):
                with patch("rag_helper.cli.process_project", side_effect=lambda **kwargs: captured.update(kwargs)):
                    run_cli(
                        default_extensions={"java"},
                        default_excludes={"target"},
                        java_extractor_cls=object,
                        adoc_extractor_cls=object,
                        xml_extractor_cls=object,
                        xsd_extractor_cls=object,
                    )

            self.assertEqual(captured["root"], project_dir.resolve())
            self.assertEqual(captured["out_dir"], (base / "out").resolve())
            self.assertEqual(captured["extensions"], {"xml", "java"})
            self.assertEqual(captured["include_globs"], ["src/**/*.java"])
            self.assertEqual(captured["exclude_globs"], ["target/**"])
            self.assertEqual(captured["limits"].max_workers, 3)
            self.assertEqual(captured["limits"].max_records_per_file, 25)
            self.assertEqual(captured["limits"].xml_mode, "smart")
            self.assertTrue(captured["resume"])
            self.assertTrue(captured["show_progress"])

    def test_run_cli_prefers_explicit_cli_over_profile_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            project_dir = base / "project"
            override_dir = base / "override-project"
            project_dir.mkdir()
            override_dir.mkdir()
            config_path = base / "rag-profile.json"
            config_path.write_text(json.dumps({
                "root": str(project_dir),
                "limits": {"max_workers": 2},
            }), encoding="utf-8")

            captured: dict = {}

            with patch("sys.argv", [
                "rag-helper",
                "--config",
                str(config_path),
                str(override_dir),
                "--max-workers",
                "5",
            ]):
                with patch("rag_helper.cli.process_project", side_effect=lambda **kwargs: captured.update(kwargs)):
                    run_cli(
                        default_extensions={"java"},
                        default_excludes={"target"},
                        java_extractor_cls=object,
                        adoc_extractor_cls=object,
                        xml_extractor_cls=object,
                        xsd_extractor_cls=object,
                    )

            self.assertEqual(captured["root"], override_dir.resolve())
            self.assertEqual(captured["limits"].max_workers, 5)

    def test_load_profile_config_reads_yaml_when_available(self) -> None:
        try:
            import yaml  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("PyYAML ist nicht installiert")

        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "rag-profile.yaml"
            config_path.write_text(
                "root: ./project\nlimits:\n  max_workers: 4\nflags:\n  progress: true\n",
                encoding="utf-8",
            )

            config, resolved = load_profile_config(config_path)

            self.assertEqual(config["root"], str((Path(tmp_dir) / "project").resolve()))
            self.assertEqual(config["max_workers"], 4)
            self.assertTrue(config["progress"])
            self.assertEqual(resolved, config_path.resolve())
