from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rag_helper.filesystem.file_filters import exclude_gitignored_files, should_include_file


class FileFilterTests(unittest.TestCase):
    def test_default_excluded_parts_still_skip_nested_build_outputs(self) -> None:
        root = Path("/workspace/project")
        path = root / "module" / "target" / "generated" / "Example.java"

        included = should_include_file(
            path=path,
            root=root,
            extensions={"java"},
            excluded_parts={"target", "build"},
        )

        self.assertFalse(included)

    def test_include_glob_limits_processing_to_matching_files(self) -> None:
        root = Path("/workspace/project")
        path = root / "docs" / "architecture.adoc"

        included = should_include_file(
            path=path,
            root=root,
            extensions={"adoc"},
            excluded_parts=set(),
            include_globs=["docs/*.adoc"],
        )

        self.assertTrue(included)

    def test_exclude_glob_matches_nested_suffix_paths(self) -> None:
        root = Path("/workspace/project")
        path = root / "module" / "target" / "dump.xml"

        included = should_include_file(
            path=path,
            root=root,
            extensions={"xml"},
            excluded_parts=set(),
            exclude_globs=["target/**", "*.xml"],
        )

        self.assertFalse(included)

    def test_exclude_gitignored_files_honors_root_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            included = root / "src" / "Main.java"
            ignored = root / "build" / "Generated.java"
            included.parent.mkdir(parents=True)
            ignored.parent.mkdir(parents=True)
            included.write_text("class Main {}", encoding="utf-8")
            ignored.write_text("class Generated {}", encoding="utf-8")
            (root / ".gitignore").write_text("build/\n", encoding="utf-8")

            filtered = exclude_gitignored_files(root, [included, ignored])

            self.assertEqual(filtered, [included])


if __name__ == "__main__":
    unittest.main()
