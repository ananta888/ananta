"""Content-based backlog deduplication."""

from agent.services.self_development.deduplication import (
    canonical_of,
    content_signature,
    group_duplicates,
    signature_owner_map,
)


def _item(todo_id, status="ready", **extra):
    base = {
        "key": "key-" + todo_id,
        "todo_id": todo_id,
        "title": "Add text budget clamp helper",
        "description": "Implement agent/services/text_budget.py",
        "acceptance_criteria": ["defines clamp_budget", "tests pass"],
        "document_goal": "Add a bounded text budget clamp helper.",
        "source_execution": {"files": ["agent/services/text_budget.py", "tests/self_development/test_text_budget.py"]},
        "status": status,
    }
    base.update(extra)
    return base


def test_signature_ignores_formatting_and_file_order():
    a = _item("A", title="Add text  budget clamp helper")
    b = _item(
        "B",
        title="add text budget clamp helper",
        source_execution={"files": ["tests/self_development/test_text_budget.py", "agent/services/text_budget.py"]},
    )
    assert content_signature(a) == content_signature(b)


def test_signature_separates_real_content_differences():
    a = _item("A")
    b = _item("B", acceptance_criteria=["defines clamp_budget", "tests pass", "no other file touched"])
    c = _item("C", description="Implement something else entirely")
    assert content_signature(a) != content_signature(b)
    assert content_signature(a) != content_signature(c)


def test_canonical_prefers_the_furthest_status():
    members = [_item("X", status="failed"), _item("Y", status="ready"), _item("Z", status="published")]
    assert canonical_of(members)["todo_id"] == "Z"
    members = [_item("B", status="ready"), _item("A", status="ready")]
    assert canonical_of(members)["todo_id"] == "A"


def test_group_duplicates_keeps_one_canonical_and_excludes_cancelled():
    items = [
        _item("A", status="failed"),
        _item("B", status="ready"),
        _item("C", status="ready"),
        _item("D", status="cancelled"),
        _item("E", description="different work"),
    ]
    groups = group_duplicates(items)
    assert len(groups) == 1
    group = groups[0]
    assert group["canonical"]["todo_id"] == "B"
    assert sorted(d["todo_id"] for d in group["duplicates"]) == ["A", "C"]


def test_singletons_do_not_group():
    assert group_duplicates([_item("A"), _item("B", description="different")]) == []


def test_signature_owner_map_prefers_first_live_item():
    items = {"k1": _item("A", status="failed"), "k2": _item("B", status="ready"), "k3": _item("C", status="cancelled")}
    owners = signature_owner_map(items)
    assert owners[content_signature(items["k1"])] == "key-A"
    assert len(owners) == 1
