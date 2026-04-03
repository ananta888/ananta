from __future__ import annotations

import compileall
from pathlib import Path
import sys
import unittest


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    ok = compileall.compile_dir(".", quiet=1)
    if not ok:
        return 1

    suite = unittest.defaultTestLoader.loadTestsFromNames([
        "tests.test_adoc_extractor",
        "tests.test_cli_config",
        "tests.test_embedding_text_modes",
        "tests.test_file_filters",
        "tests.test_generated_code_detection",
        "tests.test_framework_relations",
        "tests.test_graph_export",
        "tests.test_java_javadoc",
        "tests.test_java_member_extractor",
        "tests.test_java_method_target_resolution",
        "tests.test_parent_child_links",
        "tests.test_post_processing_features",
        "tests.test_java_role_detection",
        "tests.test_java_type_resolution",
        "tests.test_java_type_extractor",
        "tests.test_processing_limits",
        "tests.test_text_file_extractor",
    ])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
