from __future__ import annotations

from scripts.select_affected_tests import FULL_SUITE, ImportGraph, imported_modules, module_name, select

SOURCES = {
    "agent/__init__.py": "",
    "agent/core.py": "VALUE = 1\n",
    "agent/services/__init__.py": "",
    "agent/services/user.py": "from agent.core import VALUE\n",
    "agent/services/relative.py": "from ..core import VALUE\nfrom . import user\n",
    "agent/unrelated.py": "X = 2\n",
    "tests/helpers.py": "from agent.services import user\n",
    "tests/test_user.py": "from agent.services.user import VALUE\n",
    "tests/test_via_helper.py": "from tests.helpers import user\n",
    "tests/test_relative.py": "import agent.services.relative\n",
    "tests/test_docs.py": "DOC = 'docs/guide.md'\n",
    "tests/test_unrelated.py": "from agent.unrelated import X\n",
    "scripts/__init__.py": "",
    "scripts/gate.py": "SOURCES = ('.env.example',)\n",
    "tests/test_gate.py": "from scripts.gate import SOURCES\n",
}


def _graph() -> ImportGraph:
    return ImportGraph(SOURCES)


def test_module_names_follow_the_package_layout() -> None:
    assert module_name("agent/services/user.py") == "agent.services.user"
    assert module_name("agent/services/__init__.py") == "agent.services"


def test_relative_imports_resolve_against_the_importing_package() -> None:
    found = imported_modules(SOURCES["agent/services/relative.py"], relative="agent/services/relative.py")
    assert {"agent.core", "agent.services", "agent.services.user"} <= found


def test_a_changed_module_selects_every_test_that_reaches_it_transitively() -> None:
    selected = select(["agent/core.py"], _graph())
    assert not selected.full_suite
    assert set(selected.tests) == {"tests/test_user.py", "tests/test_via_helper.py", "tests/test_relative.py"}


def test_depth_limits_the_import_hops() -> None:
    selected = select(["agent/core.py"], _graph(), depth=2)
    # core <- user <- test_user (2 hops); core <- user <- helpers <- test_via_helper needs 3
    assert "tests/test_user.py" in selected.tests
    assert "tests/test_via_helper.py" not in selected.tests


def test_a_changed_test_helper_selects_its_importing_tests_only() -> None:
    assert set(select(["tests/helpers.py"], _graph()).tests) == {"tests/test_via_helper.py"}


def test_non_python_files_select_the_tests_that_name_their_path() -> None:
    assert set(select(["docs/guide.md"], _graph()).tests) == {"tests/test_docs.py"}
    assert select(["docs/other.md"], _graph()).tests == {}


def test_test_infrastructure_changes_select_the_full_suite() -> None:
    for path in ("tests/conftest.py", "tests/sub/conftest.py", "pyproject.toml", "tests/sqlite_schema_template.py"):
        selected = select([path], _graph())
        assert selected.full_suite, path
        assert FULL_SUITE in selected.tests


def test_unrelated_changes_do_not_pull_in_other_tests() -> None:
    assert set(select(["agent/unrelated.py"], _graph()).tests) == {"tests/test_unrelated.py"}


def test_non_python_files_select_tests_of_modules_that_name_them() -> None:
    assert "tests/test_gate.py" in select([".env.example"], _graph()).tests
