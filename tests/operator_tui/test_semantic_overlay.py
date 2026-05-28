"""Tests für SemanticOverlay: Panel-Extraktion, Entity-Extraktion, Hashing."""
from __future__ import annotations

import pytest

from client_surfaces.operator_tui.semantic_overlay import (
    PanelBBox,
    SemanticEntity,
    SemanticOverlay,
    build_from_operator_state,
)


class TestPanelBBox:
    def test_contains_inside(self) -> None:
        panel = PanelBBox("BODY", "body", x=0, y=10, w=120, h=20)
        assert panel.contains(0, 10) is True
        assert panel.contains(119, 29) is True

    def test_contains_outside(self) -> None:
        panel = PanelBBox("BODY", "body", x=0, y=10, w=120, h=20)
        assert panel.contains(0, 9) is False
        assert panel.contains(120, 10) is False
        assert panel.contains(0, 30) is False

    def test_to_dict_keys(self) -> None:
        panel = PanelBBox("HEADER", "header", x=0, y=0, w=80, h=8, is_active=True)
        d = panel.to_dict()
        assert d["section_id"] == "HEADER"
        assert d["semantic_id"] == "header"
        assert d["is_active"] is True


class TestSemanticOverlay:
    def test_empty_overlay_has_hash(self) -> None:
        overlay = SemanticOverlay()
        assert len(overlay.semantic_hash) == 16

    def test_panel_at_returns_correct_panel(self) -> None:
        header = PanelBBox("HEADER", "header", 0, 0, 120, 8)
        body = PanelBBox("BODY", "body", 0, 8, 120, 22)
        overlay = SemanticOverlay(panels=[header, body])
        assert overlay.panel_at(5, 3).section_id == "HEADER"
        assert overlay.panel_at(60, 15).section_id == "BODY"

    def test_panel_at_no_match_returns_none(self) -> None:
        overlay = SemanticOverlay(panels=[])
        assert overlay.panel_at(50, 50) is None

    def test_to_dict_has_required_keys(self) -> None:
        overlay = SemanticOverlay()
        d = overlay.to_dict()
        assert "panels" in d
        assert "active_panel" in d
        assert "entities" in d
        assert "screen_hash" in d
        assert "semantic_hash" in d

    def test_same_content_same_semantic_hash(self) -> None:
        o1 = SemanticOverlay(active_panel="BODY")
        o2 = SemanticOverlay(active_panel="BODY")
        assert o1.semantic_hash == o2.semantic_hash

    def test_different_active_panel_different_hash(self) -> None:
        o1 = SemanticOverlay(active_panel="HEADER")
        o2 = SemanticOverlay(active_panel="BODY")
        assert o1.semantic_hash != o2.semantic_hash


class TestBuildFromOperatorState:
    def test_minimal_state_produces_two_panels(self) -> None:
        overlay = build_from_operator_state({})
        assert len(overlay.panels) == 2
        ids = {p.section_id for p in overlay.panels}
        assert ids == {"HEADER", "BODY"}

    def test_active_panel_propagated(self) -> None:
        overlay = build_from_operator_state({"active_panel": "BODY"})
        assert overlay.active_panel == "BODY"
        body = next(p for p in overlay.panels if p.section_id == "BODY")
        assert body.is_active is True
        header = next(p for p in overlay.panels if p.section_id == "HEADER")
        assert header.is_active is False

    def test_no_active_panel_none(self) -> None:
        overlay = build_from_operator_state({})
        assert overlay.active_panel is None

    def test_snake_segments_become_entities(self) -> None:
        state = {
            "header_logo_game": {
                "snakes": [
                    {"body": [[10, 5], [9, 5], [8, 5]]}
                ]
            }
        }
        overlay = build_from_operator_state(state)
        kinds = [e.kind for e in overlay.entities]
        assert "snake_head" in kinds
        assert "snake_body" in kinds
        head = next(e for e in overlay.entities if e.kind == "snake_head")
        assert head.x == 10
        assert head.y == 5

    def test_mouse_position_becomes_entity(self) -> None:
        state = {"mouse_x": 42, "mouse_y": 17}
        overlay = build_from_operator_state(state)
        mouse = next((e for e in overlay.entities if e.kind == "mouse"), None)
        assert mouse is not None
        assert mouse.x == 42
        assert mouse.y == 17

    def test_no_mouse_no_mouse_entity(self) -> None:
        overlay = build_from_operator_state({})
        assert not any(e.kind == "mouse" for e in overlay.entities)

    def test_custom_dimensions(self) -> None:
        overlay = build_from_operator_state({}, width=80, body_start=5, body_end=20)
        header = next(p for p in overlay.panels if p.section_id == "HEADER")
        body = next(p for p in overlay.panels if p.section_id == "BODY")
        assert header.w == 80
        assert header.h == 5
        assert body.y == 5
        assert body.h == 15

    def test_invalid_snake_segments_ignored(self) -> None:
        state = {
            "header_logo_game": {
                "snakes": [
                    {"body": [None, "bad", [1], [5, 3]]}
                ]
            }
        }
        overlay = build_from_operator_state(state)
        # Only the valid segment [5, 3] should produce an entity
        snake_entities = [e for e in overlay.entities if "snake" in e.kind]
        assert len(snake_entities) == 1
        assert snake_entities[0].x == 5

    def test_screen_hash_passed_through(self) -> None:
        overlay = build_from_operator_state({}, screen_hash="abc123")
        assert overlay.screen_hash == "abc123"

    def test_multi_snake_indexing(self) -> None:
        state = {
            "header_logo_game": {
                "snakes": [
                    {"body": [[1, 1]]},
                    {"body": [[2, 2]]},
                ]
            }
        }
        overlay = build_from_operator_state(state)
        ids = {e.semantic_id for e in overlay.entities}
        assert "snake_0_seg_0" in ids
        assert "snake_1_seg_0" in ids

    def test_free_mode_adds_tutor_and_chat_panels(self) -> None:
        state = {
            "header_logo_game": {
                "active": True,
                "free_mode": True,
                "chat_state": {"chat_focus": False},
            }
        }
        overlay = build_from_operator_state(state, width=120, body_start=8, body_end=30)
        panel_ids = {p.section_id for p in overlay.panels}
        assert "TUTOR_AI" in panel_ids
        assert "CHAT" in panel_ids

    def test_chat_focus_marks_chat_panel_active(self) -> None:
        state = {
            "header_logo_game": {
                "active": True,
                "free_mode": True,
                "chat_state": {"chat_focus": True},
            }
        }
        overlay = build_from_operator_state(state, width=120, body_start=8, body_end=30)
        chat_panel = next(p for p in overlay.panels if p.section_id == "CHAT")
        tutor_panel = next(p for p in overlay.panels if p.section_id == "TUTOR_AI")
        assert chat_panel.is_active is True
        assert tutor_panel.is_active is False

    def test_panel_at_prefers_chat_over_body_in_split_view(self) -> None:
        state = {
            "header_logo_game": {
                "active": True,
                "free_mode": True,
                "chat_state": {"chat_focus": True},
            }
        }
        overlay = build_from_operator_state(state, width=120, body_start=8, body_end=30)
        hit = overlay.panel_at(90, 25)
        assert hit is not None
        assert hit.section_id == "CHAT"
