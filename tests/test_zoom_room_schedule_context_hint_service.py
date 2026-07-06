from agent.services.classroom.zoom_room_schedule_context_hint_service import build_context_hints


def test_room_and_schedule_hints_are_bounded():
    cfg = {
        "classroom": {
            "room_mappings": {"r1": {"group": "g1", "module_scope": "M1"}},
            "schedule": [{"day": "mon", "start": "09:00", "end": "10:00", "module_id": "M1", "task_id": "A1"}],
        }
    }
    before = build_context_hints(zoom_room_id="r1", timestamp="2026-07-06T08:59:00Z", cfg=cfg)
    during = build_context_hints(zoom_room_id="r1", timestamp="2026-07-06T09:00:00Z", cfg=cfg)
    assert len(before["ranked_context_hints"]) == 1
    assert len(during["ranked_context_hints"]) == 2
    assert during["retrieval_filters"] == {"module_scope": "M1", "task_scope": "A1"}
    assert all(h["confidence"] != "strong" for h in during["ranked_context_hints"])
