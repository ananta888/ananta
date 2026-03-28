from __future__ import annotations

import compileall
import sys
import unittest


def main() -> int:
    ok = compileall.compile_dir(".", quiet=1)
    if not ok:
        return 1

    suite = unittest.defaultTestLoader.loadTestsFromNames([
        "tests.test_java_member_extractor",
        "tests.test_java_type_extractor",
    ])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
