"""Removed-symbol import integrity for the publication verify chain.

The published regression: a change set deleted ``split_ordered``/``pack_parts``
while ``long_context_coordinator`` kept importing them, and the behavioural
profile passed anyway. The gate must refuse exactly that class before landing.
"""

from types import SimpleNamespace

import pytest

from agent.services.self_development.source_integrity import (
    assert_removed_symbols_unreferenced,
    removed_imported_symbols,
)

MODULE = "agent.services.ctxmod"
BEFORE = '"""ctxmod."""\n\n\ndef helper():\n    return 1\n\n\nclass Widget:\n    pass\n'
AFTER = '"""ctxmod."""\n\n\ndef replacement():\n    return 2\n'


def test_from_import_of_a_removed_name_is_reported():
    tree = {"consumer.py": "from agent.services.ctxmod import helper\n"}
    assert removed_imported_symbols(BEFORE, AFTER, MODULE, tree) == ["helper"]


def test_removed_name_without_importers_passes():
    tree = {"consumer.py": "from agent.services.ctxmod import replacement\n"}
    assert removed_imported_symbols(BEFORE, AFTER, MODULE, tree) == []


def test_kept_names_are_not_reported():
    before = "def kept():\n    return 1\n"
    after = "def kept():\n    return 1\n\n\ndef added():\n    return 2\n"
    tree = {"consumer.py": "from agent.services.ctxmod import kept\n"}
    assert removed_imported_symbols(before, after, MODULE, tree) == []


def test_dotted_and_aliased_attribute_use_is_reported():
    tree = {
        "dotted.py": ("import agent.services.ctxmod\nagent.services.ctxmod.helper()\n"),
        "aliased.py": "import agent.services.ctxmod as m\nm.helper()\n",
        "parent.py": "from agent.services import ctxmod\nctxmod.helper()\n",
    }
    assert removed_imported_symbols(BEFORE, AFTER, MODULE, tree) == ["helper"]


def test_star_import_is_conservatively_reported():
    tree = {"consumer.py": "from agent.services.ctxmod import *\n"}
    assert removed_imported_symbols(BEFORE, AFTER, MODULE, tree) == ["Widget", "helper"]


def test_same_name_from_a_different_module_is_not_a_hit():
    tree = {"consumer.py": "from agent.otherpkg import helper\nhelper = helper\n"}
    assert removed_imported_symbols(BEFORE, AFTER, MODULE, tree) == []


def test_plain_local_uses_of_the_name_are_not_import_references():
    tree = {"scripts.py": "helper = 1\nprint(helper)\n"}
    assert removed_imported_symbols(BEFORE, AFTER, MODULE, tree) == []


def test_unparseable_tree_files_are_ignored():
    tree = {"broken.py": "def helper(:\n", "consumer.py": "from agent.services.ctxmod import helper\n"}
    assert removed_imported_symbols(BEFORE, AFTER, MODULE, tree) == ["helper"]


def test_added_file_has_no_before_state_to_remove_from():
    tree = {"consumer.py": "from agent.services.ctxmod import helper\n"}
    assert removed_imported_symbols("", AFTER, MODULE, tree) == []


def _git(root, *args):
    import subprocess

    result = subprocess.run(
        ["git", "-c", "safe.directory=" + str(root), *args],
        cwd=root,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout.decode().strip()


def _checkout_with_consumer(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-b", "ananta/self-development")
    _git(checkout, "config", "user.name", "Test")
    _git(checkout, "config", "user.email", "test@example.com")
    module_dir = checkout / "agent" / "services"
    module_dir.mkdir(parents=True)
    (module_dir / "ctxmod.py").write_text(BEFORE, encoding="utf-8")
    (module_dir / "consumer.py").write_text("from agent.services.ctxmod import helper\n", encoding="utf-8")
    _git(checkout, "add", "--", ".")
    _git(checkout, "commit", "-m", "chore: base")
    revision = _git(checkout, "rev-parse", "HEAD")
    return checkout, revision


def _seal(tmp_path, task_id, after_source):
    seal = tmp_path / "control" / "sealed" / task_id
    target = seal / "agent" / "services" / "ctxmod.py"
    target.parent.mkdir(parents=True)
    target.write_text(after_source, encoding="utf-8")
    return tmp_path / "control"


def test_glue_refuses_a_sealed_change_set_still_imported_by_the_checkout(tmp_path):
    checkout, revision = _checkout_with_consumer(tmp_path)
    control_root = _seal(tmp_path, "t-1", AFTER)
    policy = SimpleNamespace(checkout=checkout, root=control_root)
    item = {"hashes": {"agent/services/ctxmod.py": "0" * 64}, "revision": revision, "task_id": "t-1"}

    with pytest.raises(ValueError, match="removed_symbol_still_imported:agent/services/ctxmod.py"):
        assert_removed_symbols_unreferenced(policy, item)


def test_glue_passes_when_the_importer_follows_the_removed_name(tmp_path):
    checkout, revision = _checkout_with_consumer(tmp_path)
    # The consumer in the checkout is updated by the change set itself: the
    # sealed overlay makes the gate judge sibling files by their new content.
    consumer_after = "from agent.services.ctxmod import replacement\n"
    control_root = _seal(tmp_path, "t-2", AFTER)
    consumer = control_root / "sealed" / "t-2" / "agent" / "services" / "consumer.py"
    consumer.write_text(consumer_after, encoding="utf-8")
    policy = SimpleNamespace(checkout=checkout, root=control_root)
    item = {
        "hashes": {"agent/services/ctxmod.py": "0" * 64, "agent/services/consumer.py": "1" * 64},
        "revision": revision,
        "task_id": "t-2",
    }

    assert_removed_symbols_unreferenced(policy, item)


def test_glue_passes_when_the_changed_file_is_new(tmp_path):
    checkout, _revision = _checkout_with_consumer(tmp_path)
    control_root = _seal(tmp_path, "t-3", AFTER)
    policy = SimpleNamespace(checkout=checkout, root=control_root)
    item = {
        "hashes": {"agent/services/newmod.py": "2" * 64},
        "revision": "HEAD",
        "task_id": "t-3",
    }

    policy.checkout = checkout
    assert_removed_symbols_unreferenced(policy, item)
