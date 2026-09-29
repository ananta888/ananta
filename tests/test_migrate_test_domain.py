from __future__ import annotations

from scripts.migrate_test_domain import deepen_file_anchors, rewrite_references


def test_file_anchors_keep_pointing_at_the_same_directories_one_level_deeper() -> None:
    source = (
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "UP = Path(__file__).parents[2]\n"
        "HERE = Path(__file__).resolve().parent / 'fixtures'\n"
        "BASE = Path(__file__).parent.parent\n"
        "LEGACY = os.path.dirname(__file__)\n"
        "SIBLING = Path(__file__).with_name('helper.py')\n"
    )
    assert deepen_file_anchors(source) == (
        "ROOT = Path(__file__).resolve().parents[2]\n"
        "UP = Path(__file__).parents[3]\n"
        "HERE = Path(__file__).resolve().parent.parent / 'fixtures'\n"
        "BASE = Path(__file__).parent.parent.parent\n"
        "LEGACY = os.path.dirname(os.path.dirname(__file__))\n"
        "SIBLING = Path(__file__).with_name('helper.py')\n"
    )


def test_module_names_and_paths_are_rewritten_exactly() -> None:
    moves = {"test_meet_media.py": "meet", "meet_fixture.py": "meet", "meet_bridge.mjs": "meet"}
    source = (
        "from tests.test_meet_media import turn\n"
        "import tests.meet_fixture as fixture\n"
        "monkeypatch.setattr('tests.test_meet_media.clock', clock)\n"
        "run('tests/test_meet_media.py::test_x')\n"
        "bridge = 'tests/meet_bridge.mjs'\n"
        "untouched = 'tests/test_meet_media_other.py', tests.test_meet_media_other, 'xtests/test_meet_media.py'\n"
    )
    assert rewrite_references(source, moves) == (
        "from tests.meet.test_meet_media import turn\n"
        "import tests.meet.meet_fixture as fixture\n"
        "monkeypatch.setattr('tests.meet.test_meet_media.clock', clock)\n"
        "run('tests/meet/test_meet_media.py::test_x')\n"
        "bridge = 'tests/meet/meet_bridge.mjs'\n"
        "untouched = 'tests/test_meet_media_other.py', tests.test_meet_media_other, 'xtests/test_meet_media.py'\n"
    )


def test_already_moved_references_are_not_rewritten_twice() -> None:
    source = "from tests.meet.test_meet_media import turn\npath = 'tests/meet/test_meet_media.py'\n"
    assert rewrite_references(source, {"test_meet_media.py": "meet"}) == source


def test_from_tests_imports_follow_the_moved_modules() -> None:
    moves = {"test_meet_integration.py": "meet"}
    source = "from tests import test_meet_integration\n    from tests import test_meet_integration as mi, test_other\n"
    assert rewrite_references(source, moves) == (
        "from tests.meet import test_meet_integration\n"
        "    from tests.meet import test_meet_integration as mi\n"
        "    from tests import test_other\n"
    )


def test_relative_js_imports_are_deepened() -> None:
    from scripts.migrate_test_domain import deepen_js_relative_imports

    source = "import { a } from '../agent/x.mjs';\nconst b = require('../lib/b.js');\nimport './sibling.mjs';\n"
    assert deepen_js_relative_imports(source) == (
        "import { a } from '../../agent/x.mjs';\nconst b = require('../../lib/b.js');\nimport './sibling.mjs';\n"
    )


def test_hashed_sources_are_the_named_repository_files_outside_tests_and_the_pinning_dirs() -> None:
    from scripts.migrate_test_domain import ROOT, hashed_sources

    text = '"docs/testing.md", tests/test_x.py, scripts/migrate_test_domain.py, docs/does-not-exist.md, xdocs/testing.md'
    assert hashed_sources(text) == {ROOT / "docs/testing.md"}
