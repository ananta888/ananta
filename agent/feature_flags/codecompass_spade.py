"""Feature flags for the SPADE / CMake File API importer.

Defaults are ``off``. SPADE has no stable release at review time; the
imported ref-impl is pinned by commit.
"""
from __future__ import annotations

GROUP = "spade"


def flags() -> dict[str, bool]:
    return {
        "cmake_extractor_enabled": False,
        "ctest_runner_enabled": False,
    }