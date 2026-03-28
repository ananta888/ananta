from __future__ import annotations

import unittest
from pathlib import Path

from rag_helper.filesystem.file_filters import should_include_file


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


if __name__ == "__main__":
    unittest.main()
