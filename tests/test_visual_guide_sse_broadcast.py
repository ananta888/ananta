"""Tests for VisualGuideService SSE broadcasting (Option B) and manual guide (Option A).

Covers:
- handle_ui_tick broadcasts a 'guide' event when the LLM answer contains __GUIDE__
- handle_ui_tick broadcasts a 'candidates' event in multi-candidate mode
- handle_region_explain broadcasts a 'guide' event with readable summary prefix
- Candidate selection messages ([region-explain] candidate:N) are logged but do not spawn AI
- handle_manual_guide broadcasts a 'guide' event and writes to ananta-visual session
- handle_region_explain message includes human-readable prefix before __GUIDE__:
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _fake_pug_settings(enabled: bool = True, candidates: int = 1) -> dict:
    return {
        "predictive_guide_enabled": enabled,
        "predictive_guide_multi_candidates": candidates,
        "predictive_guide_log_deltas_only": False,
    }


@pytest.fixture(autouse=True)
def _clean_visual_state():
    import agent.services.visual_guide.service as svc_mod

    original = svc_mod._visual_state
    svc_mod._visual_state = {}
    yield
    svc_mod._visual_state = original


class TestVisualGuideBroadcast:
    """VisualGuideService emits snake events for guides and candidates."""

    def test_ui_tick_broadcasts_guide_event(self):
        from agent.services.visual_guide.service import VisualGuideService

        svc = VisualGuideService()
        broadcasts = []
        appended = []

        with patch("agent.services.visual_guide.service._visual_session_settings",
                   return_value=_fake_pug_settings(enabled=True, candidates=1)), \
             patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message",
                   side_effect=lambda **kw: appended.append(kw)), \
             patch("agent.services.visual_guide.service._broadcast_snake_event",
                   side_effect=lambda sid, et, pl: broadcasts.append((sid, et, pl))):

            with patch.object(svc, "_call_llm_for_ui_tick",
                               return_value='Hallo\n\n__GUIDE__:{"steps":[{"waypoint":"btn.ok","bubble":"OK","delay_ms":2000}]}'):
                svc.handle_ui_tick("snake-a", "snapshot", "/test-page", [])

        assert len(appended) == 1
        assert len(broadcasts) == 1
        sid, etype, payload = broadcasts[0]
        assert sid == "snake-a"
        assert etype == "guide"
        assert payload["trigger_type"] == "ui_tick"
        assert len(payload["steps"]) == 1
        assert payload["steps"][0]["waypoint"] == "btn.ok"

    def test_ui_tick_multi_candidate_broadcasts_candidates_event(self):
        from agent.services.visual_guide.service import VisualGuideService

        svc = VisualGuideService()
        broadcasts = []

        with patch("agent.services.visual_guide.service._visual_session_settings",
                   return_value=_fake_pug_settings(enabled=True, candidates=3)), \
             patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message"), \
             patch("agent.services.visual_guide.service._broadcast_snake_event",
                   side_effect=lambda sid, et, pl: broadcasts.append((sid, et, pl))):

            answer = (
                '__CANDIDATES__:['
                '{"label":"primary","bubble":"Hauptvorschlag","steps":[{"waypoint":"a","bubble":"A"}]},'
                '{"label":"alt-1","bubble":"Alternative","steps":[{"waypoint":"b","bubble":"B"}]}'
                ']'
            )
            with patch.object(svc, "_call_llm_for_ui_tick", return_value=answer):
                svc.handle_ui_tick("snake-b", "snapshot", "/test-page", [])

        assert len(broadcasts) == 1
        sid, etype, payload = broadcasts[0]
        assert sid == "snake-b"
        assert etype == "candidates"
        assert len(payload["candidates"]) == 2
        assert payload["candidates"][0]["label"] == "primary"

    def test_region_explain_broadcasts_guide_event(self):
        from agent.services.visual_guide.service import VisualGuideService

        svc = VisualGuideService()
        broadcasts = []

        with patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message"), \
             patch("agent.services.visual_guide.service._broadcast_snake_event",
                   side_effect=lambda sid, et, pl: broadcasts.append((sid, et, pl))):

            with patch.object(svc, "_call_llm_for_region_explain",
                               return_value=["Erklärung"]):
                svc.handle_region_explain("snake-c", [
                    {"bubble": "Button", "x": 100.0, "y": 200.0, "waypoint": "btn.ok"},
                ], "/page")

        assert len(broadcasts) == 1
        sid, etype, payload = broadcasts[0]
        assert sid == "snake-c"
        assert etype == "guide"
        assert payload["trigger_type"] == "region_explain"
        assert len(payload["steps"]) == 1


class TestCandidateSelectionLogging:
    """[region-explain] candidate:N messages are logged without spawning AI."""

    @pytest.fixture
    def client(self):
        from agent.routes.snakes import _snakes, snakes_bp
        from flask import Flask

        app = Flask(__name__)
        app.register_blueprint(snakes_bp)

        _snakes["sel-snake"] = {
            "id": "sel-snake",
            "token": "sel-token",
            "active": True,
            "name": "test",
            "role": "viewer",
            "color": "mint",
        }

        yield app.test_client()

        _snakes.pop("sel-snake", None)

    def test_candidate_selection_does_not_spawn_region_explain(self, client):
        from agent.routes.snakes_execution_routes import _VISUAL_GUIDE_EXECUTOR

        with patch("agent.routes.snakes_execution_routes._append_room_ai_message") as mock_append, \
             patch.object(_VISUAL_GUIDE_EXECUTOR, "submit") as mock_submit:
            resp = client.post(
                "/snakes/sel-snake/chat/messages",
                json={
                    "id": "msg-1",
                    "channel_type": "room",
                    "visibility": "system",
                    "text": "[region-explain] candidate:0 | primary | Test",
                    "session_id": "ananta-visual",
                },
                headers={"Authorization": "Bearer sel-token"},
            )

        assert resp.status_code == 202
        assert mock_append.call_count == 1
        assert mock_append.call_args.kwargs["text"].startswith("[region-explain] candidate:")
        mock_submit.assert_not_called()


class TestRegionExplainReadableSummary:
    """handle_region_explain messages must include a human-readable prefix before __GUIDE__:."""

    def test_llm_path_message_has_readable_prefix(self):
        from agent.services.visual_guide.service import VisualGuideService

        svc = VisualGuideService()
        appended_texts: list[str] = []

        with patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message",
                   side_effect=lambda **kw: appended_texts.append(kw["text"])), \
             patch("agent.services.visual_guide.service._broadcast_snake_event"):

            with patch.object(svc, "_call_llm_for_region_explain",
                               return_value=["Erklärung"]):
                svc.handle_region_explain("snake-x", [
                    {"bubble": "Button", "x": 50.0, "y": 80.0, "waypoint": "btn.save"},
                ], "/settings")

        assert len(appended_texts) == 1
        text = appended_texts[0]
        assert "__GUIDE__:" in text
        assert text.index("__GUIDE__:") > 0, "readable prefix must precede __GUIDE__:"
        assert "/settings" in text

    def test_rule_path_message_has_readable_prefix(self):
        from agent.services.visual_guide.service import VisualGuideService
        from agent.services.visual_guide.rule_engine import RuleEngine

        svc = VisualGuideService()
        appended_texts: list[str] = []

        with patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message",
                   side_effect=lambda **kw: appended_texts.append(kw["text"])), \
             patch("agent.services.visual_guide.service._broadcast_snake_event"), \
             patch.object(RuleEngine, "lookup_region_step", return_value="Tipp für den Button"):

            svc.handle_region_explain("snake-x", [
                {"bubble": "Button", "x": 50.0, "y": 80.0, "waypoint": "btn.save"},
            ], "/settings")

        assert len(appended_texts) == 1
        text = appended_texts[0]
        assert "__GUIDE__:" in text
        assert text.index("__GUIDE__:") > 0
        assert "Regel" in text


class TestHandleManualGuide:
    """handle_manual_guide broadcasts a guide event and writes to ananta-visual."""

    def test_broadcasts_guide_event_when_llm_returns_guide_steps(self):
        from agent.services.visual_guide.service import VisualGuideService

        svc = VisualGuideService()
        broadcasts: list[tuple] = []
        appended_texts: list[str] = []
        llm_answer = 'Hier ist dein Guide.\n\n__GUIDE__:{"steps":[{"waypoint":"btn.ok","bubble":"Klick hier","delay_ms":2000}]}'

        with patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message",
                   side_effect=lambda **kw: appended_texts.append(kw["text"])), \
             patch("agent.services.visual_guide.service._broadcast_snake_event",
                   side_effect=lambda sid, et, pl: broadcasts.append((sid, et, pl))):

            with patch.object(svc, "_call_llm_for_manual_guide", return_value=llm_answer):
                svc.handle_manual_guide("snake-m", "Zeige mir den Speichern-Button", snapshot="", route="/page")

        assert len(broadcasts) == 1
        sid, etype, payload = broadcasts[0]
        assert sid == "snake-m"
        assert etype == "guide"
        assert payload["trigger_type"] == "manual"
        assert len(payload["steps"]) == 1

        assert len(appended_texts) == 1
        assert appended_texts[0] == llm_answer

    def test_no_broadcast_when_llm_returns_no_guide_steps(self):
        from agent.services.visual_guide.service import VisualGuideService

        svc = VisualGuideService()
        broadcasts: list[tuple] = []

        with patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message"), \
             patch("agent.services.visual_guide.service._broadcast_snake_event",
                   side_effect=lambda sid, et, pl: broadcasts.append((sid, et, pl))):

            with patch.object(svc, "_call_llm_for_manual_guide", return_value="Ich weiß es leider nicht."):
                svc.handle_manual_guide("snake-m", "Unbekannter Intent", snapshot="", route="")

        assert len(broadcasts) == 0

    def test_empty_intent_is_noop(self):
        from agent.services.visual_guide.service import VisualGuideService

        svc = VisualGuideService()

        with patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message") as mock_append:

            svc.handle_manual_guide("snake-m", intent="")

        mock_append.assert_not_called()

    def test_rate_limited_guide_writes_message_to_visual_session(self):
        from agent.services.visual_guide.service import VisualGuideService

        svc = VisualGuideService()
        appended: list[dict] = []

        with patch("agent.services.visual_guide.service._background_threads_disabled",
                   return_value=False), \
             patch("agent.services.visual_guide.service._append_room_ai_message",
                   side_effect=lambda **kw: appended.append(kw)):
            # Exhaust rate limit
            for _ in range(10):
                svc._rate_limit_timestamps["snake-rl"] = [9e9] * 10
            svc.handle_manual_guide("snake-rl", "Teste Rate-Limit", snapshot="", route="")

        assert len(appended) == 1
        assert "Rate-Limit" in appended[0]["text"]
        assert appended[0]["session_id"] == "ananta-visual"
