from __future__ import annotations

from client_surfaces.operator_tui.models import OperatorState, TuiTab
from client_surfaces.operator_tui.tab_manager import (
    activate_tab,
    close_tab,
    find_tab,
    open_or_activate_tab,
    save_scroll_to_active_tab,
    tab_label_for_chat_preview,
    tab_label_for_section,
    tab_positions_for_render,
)


def _state() -> OperatorState:
    return OperatorState(endpoint="http://localhost:5000")


# ── tab_label helpers ────────────────────────────────────────────────────────

def test_tab_label_for_section_returns_title() -> None:
    assert tab_label_for_section("goals") == "Goals"
    assert tab_label_for_section("dashboard") == "Dashboard"
    assert tab_label_for_section("tasks") == "Tasks"


def test_tab_label_for_chat_preview_short() -> None:
    assert tab_label_for_chat_preview("Kurz") == "Kurz"


def test_tab_label_for_chat_preview_truncates() -> None:
    result = tab_label_for_chat_preview("Hallo wie geht es dir heute")
    assert len(result) <= 10
    assert result.endswith("…")


def test_tab_label_for_chat_preview_empty() -> None:
    assert tab_label_for_chat_preview("") == ""
    assert tab_label_for_chat_preview("   ") == ""


# ── open_or_activate_tab ────────────────────────────────────────────────────

def test_open_first_tab_creates_it() -> None:
    s = open_or_activate_tab(_state(), section_id="goals", kind="section", label="Goals")
    assert len(s.open_tabs) == 1
    assert s.open_tabs[0].id == "section:goals"
    assert s.open_tabs[0].label == "Goals"
    assert s.active_tab_id == "section:goals"


def test_open_second_tab_appends() -> None:
    s = _state()
    s = open_or_activate_tab(s, section_id="dashboard", kind="section", label="Dashboard")
    s = open_or_activate_tab(s, section_id="goals", kind="section", label="Goals")
    assert len(s.open_tabs) == 2
    assert s.active_tab_id == "section:goals"


def test_open_duplicate_section_tab_activates_existing() -> None:
    s = _state()
    s = open_or_activate_tab(s, section_id="goals", kind="section", label="Goals")
    s = open_or_activate_tab(s, section_id="dashboard", kind="section", label="Dashboard")
    s2 = open_or_activate_tab(s, section_id="goals", kind="section", label="Goals")
    assert len(s2.open_tabs) == 2
    assert s2.active_tab_id == "section:goals"


def test_open_chat_viewport_tab_allows_multiple() -> None:
    s = _state()
    s = open_or_activate_tab(s, section_id="dashboard", kind="chat_viewport", label="Hallo…",
                              viewport_state={"scroll_offset": 0, "preview": "Hallo erste Nachricht"})
    s2 = open_or_activate_tab(s, section_id="dashboard", kind="chat_viewport", label="Welt…",
                               viewport_state={"scroll_offset": 0, "preview": "Welt zweite Nachricht"})
    assert len(s2.open_tabs) == 2
    assert s2.open_tabs[0].id != s2.open_tabs[1].id
    assert s2.active_tab_id == s2.open_tabs[1].id


def test_open_duplicate_chat_viewport_tab_activates_existing() -> None:
    s = _state()
    viewport_state = {"scroll_offset": 0, "preview": "Dieselbe lange Nachricht"}
    s = open_or_activate_tab(s, section_id="dashboard", kind="chat_viewport", label="Dieselbe…",
                             viewport_state=viewport_state)
    first_tab_id = s.active_tab_id
    s = s.with_updates(active_tab_id="")
    s2 = open_or_activate_tab(s, section_id="dashboard", kind="chat_viewport", label="Dieselbe…",
                              viewport_state=viewport_state)
    assert len(s2.open_tabs) == 1
    assert s2.active_tab_id == first_tab_id


# ── close_tab ───────────────────────────────────────────────────────────────

def test_close_only_tab_creates_dashboard() -> None:
    s = open_or_activate_tab(_state(), section_id="goals", kind="section", label="Goals")
    s2 = close_tab(s, "section:goals")
    assert len(s2.open_tabs) == 1
    assert s2.open_tabs[0].section_id == "dashboard"
    assert s2.active_tab_id == "section:dashboard"


def test_close_active_tab_activates_left_neighbor() -> None:
    s = _state()
    s = open_or_activate_tab(s, section_id="dashboard", kind="section", label="Dashboard")
    s = open_or_activate_tab(s, section_id="goals", kind="section", label="Goals")
    s = open_or_activate_tab(s, section_id="tasks", kind="section", label="Tasks")
    # close Tasks (active, rightmost)
    s2 = close_tab(s, "section:tasks")
    assert s2.active_tab_id == "section:goals"
    assert len(s2.open_tabs) == 2


def test_close_active_first_tab_activates_right_neighbor() -> None:
    s = _state()
    s = open_or_activate_tab(s, section_id="dashboard", kind="section", label="Dashboard")
    s = open_or_activate_tab(s, section_id="goals", kind="section", label="Goals")
    # make dashboard active
    s = s.with_updates(active_tab_id="section:dashboard")
    s2 = close_tab(s, "section:dashboard")
    assert s2.active_tab_id == "section:goals"


def test_close_inactive_tab_leaves_active_unchanged() -> None:
    s = _state()
    s = open_or_activate_tab(s, section_id="dashboard", kind="section", label="Dashboard")
    s = open_or_activate_tab(s, section_id="goals", kind="section", label="Goals")
    s2 = close_tab(s, "section:dashboard")
    assert s2.active_tab_id == "section:goals"
    assert len(s2.open_tabs) == 1


# ── activate_tab ────────────────────────────────────────────────────────────

def test_activate_section_tab_sets_section_id() -> None:
    s = _state()
    s = open_or_activate_tab(s, section_id="dashboard", kind="section", label="Dashboard")
    s = open_or_activate_tab(s, section_id="goals", kind="section", label="Goals")
    s = s.with_updates(active_tab_id="section:dashboard", section_id="dashboard")
    new_state, _ = activate_tab(s, "section:goals")
    assert new_state.section_id == "goals"
    assert new_state.active_tab_id == "section:goals"


def test_activate_section_tab_clears_viewport() -> None:
    s = _state()
    s = open_or_activate_tab(s, section_id="goals", kind="section", label="Goals")
    new_state, game_out = activate_tab(s, "section:goals", game={"visual_viewport_enabled": True})
    assert game_out.get("visual_viewport_enabled") is False
    assert dict(game_out.get("visual_viewport") or {}).get("enabled") is False


def test_activate_chat_viewport_tab_enables_viewport() -> None:
    s = _state()
    s = open_or_activate_tab(s, section_id="dashboard", kind="chat_viewport", label="Hallo…",
                              viewport_state={"scroll_offset": 42})
    tab_id = s.active_tab_id
    new_state, game_out = activate_tab(s, tab_id, game={})
    assert game_out.get("visual_viewport_enabled") is True
    assert dict(game_out.get("visual_viewport") or {}).get("enabled") is True
    assert game_out.get("scroll_offset_center_viewport") == 42


# ── save_scroll_to_active_tab ────────────────────────────────────────────────

def test_save_scroll_persists_to_chat_viewport_tab() -> None:
    s = _state()
    s = open_or_activate_tab(s, section_id="dashboard", kind="chat_viewport", label="Hallo…",
                              viewport_state={"scroll_offset": 0})
    tab_id = s.active_tab_id
    s2 = save_scroll_to_active_tab(s, 77)
    tab = find_tab(s2, tab_id=tab_id)
    assert tab is not None
    assert (tab.viewport_state or {}).get("scroll_offset") == 77


def test_save_scroll_does_not_affect_section_tab() -> None:
    s = open_or_activate_tab(_state(), section_id="goals", kind="section", label="Goals")
    s2 = save_scroll_to_active_tab(s, 99)
    assert s2.open_tabs == s.open_tabs


def test_two_chat_viewport_tabs_independent_scroll() -> None:
    s = _state()
    s = open_or_activate_tab(s, section_id="dashboard", kind="chat_viewport", label="A…",
                              viewport_state={"scroll_offset": 0, "preview": "A"})
    tab_a = s.active_tab_id
    # Simulate a second message tab with an independent scroll state.
    tab_b_id = "chat:99999"
    tab_b = TuiTab(id=tab_b_id, kind="chat_viewport", section_id="dashboard",  # type: ignore[arg-type]
                   label="B…", viewport_state={"scroll_offset": 0})
    s = s.with_updates(open_tabs=s.open_tabs + (tab_b,), active_tab_id=tab_b_id)
    s = save_scroll_to_active_tab(s, 50)
    # tab_a should be unchanged
    tab_a_obj = find_tab(s, tab_id=tab_a)
    assert (tab_a_obj.viewport_state or {}).get("scroll_offset") == 0
    # tab_b should have 50
    tab_b_obj = find_tab(s, tab_id=tab_b_id)
    assert (tab_b_obj.viewport_state or {}).get("scroll_offset") == 50


# ── tab_positions_for_render ─────────────────────────────────────────────────

def test_tab_positions_empty_state() -> None:
    assert tab_positions_for_render(_state(), width=120) == []


def test_tab_positions_count_matches_visible_tabs() -> None:
    s = _state()
    s = open_or_activate_tab(s, section_id="dashboard", kind="section", label="Dashboard")
    s = open_or_activate_tab(s, section_id="goals", kind="section", label="Goals")
    positions = tab_positions_for_render(s, width=120)
    assert len(positions) == 2


def test_tab_positions_x_are_non_overlapping() -> None:
    s = _state()
    for sec_id, label in [("dashboard", "Dashboard"), ("goals", "Goals"), ("tasks", "Tasks")]:
        s = open_or_activate_tab(s, section_id=sec_id, kind="section", label=label)
    positions = tab_positions_for_render(s, width=120)
    for i in range(1, len(positions)):
        assert positions[i].label_x1 > positions[i - 1].close_x


# ── SeedTemplateCatalog ───────────────────────────────────────────────────────

def test_seed_template_catalog_loads_all_templates() -> None:
    from agent.services.seed_template_catalog import get_seed_template_catalog
    cat = get_seed_template_catalog()
    tpls = cat.get_all_templates()
    assert len(tpls) == 27
    names = {t["name"] for t in tpls}
    assert "Scrum - Product Owner" in names
    assert "TDD - Refactor Verifier" in names
    assert "Research Evolution - Review Gate Owner" in names


def test_seed_template_catalog_expands_appendixes() -> None:
    from agent.services.seed_template_catalog import get_seed_template_catalog
    cat = get_seed_template_catalog()
    po = next(t for t in cat.get_templates_for_team_type("Scrum") if t["name"] == "Scrum - Product Owner")
    assert "SOLID" in po["prompt_template"]
    assert "{{appendix:" not in po["prompt_template"]


def test_seed_template_catalog_opencode_has_both_appendixes() -> None:
    from agent.services.seed_template_catalog import get_seed_template_catalog
    cat = get_seed_template_catalog()
    dev = next(t for t in cat.get_templates_for_team_type("Scrum") if t["name"] == "OpenCode Scrum - Developer")
    assert "Execution cascade" in dev["prompt_template"]
    assert "SOLID" in dev["prompt_template"]


def test_seed_template_catalog_role_profile_defaults() -> None:
    from agent.services.seed_template_catalog import get_seed_template_catalog
    cat = get_seed_template_catalog()
    d = cat.get_role_profile_defaults("TDD", "Test Driver")
    assert d["risk_profile"] == "high"
    assert "red_before_green_evidence" in d["verification_defaults"]["gates"]


def test_seed_template_catalog_unknown_team_type_returns_empty() -> None:
    from agent.services.seed_template_catalog import get_seed_template_catalog
    cat = get_seed_template_catalog()
    assert cat.get_templates_for_team_type("NonExistent") == []
    assert cat.get_role_specs_for_team_type("NonExistent") == []
    assert cat.get_role_profile_defaults("NonExistent", "Any") == {}


def test_seed_template_catalog_all_team_types_covered() -> None:
    from agent.services.seed_template_catalog import get_seed_template_catalog
    cat = get_seed_template_catalog()
    types = set(cat.known_team_types())
    expected = {
        "Scrum",
        "Kanban",
        "Research",
        "Code-Repair",
        "TDD",
        "Security-Review",
        "Release-Prep",
        "Research-Evolution",
    }
    assert expected == types


def test_seed_template_catalog_schema_validates() -> None:
    """Catalog file must pass JSON schema validation (uses jsonschema)."""
    from agent.services.seed_template_catalog import SeedTemplateCatalog
    cat = SeedTemplateCatalog()
    # _load raises ValueError on schema violations
    cat._load()
    assert cat._catalog is not None
