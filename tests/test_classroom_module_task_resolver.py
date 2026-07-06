from agent.services.classroom.module_task_resolver_service import ModuleTaskResolverService


def test_explicit_hint_wins_and_material_evidence_is_grounded():
    seen_filters = []

    def search(query, filters):
        seen_filters.append(filters)
        return [
            {
                "module_id": "B",
                "task_id": "B-1",
                "title": "Webhook",
                "score": 0.9,
                "file": "b.md",
                "excerpt": "Webhook Trigger",
            }
        ]

    service = ModuleTaskResolverService(search_fn=search)
    result = service.resolve(
        event={"task_id_hint": "A-1", "module_id_hint": "A", "text_segment": "Webhook"},
        detection={"evidence_spans": []},
        hints={"retrieval_filters": {"module_scope": "B"}, "ranked_context_hints": [{"kind": "room"}]},
    )
    assert result["ranked_candidates"][0]["task_id"] == "A-1"
    assert result["ranked_candidates"][1]["evidence_refs"][0]["kind"] == "retrieval_chunk"
    assert seen_filters == [{"module_scope": "B"}]


def test_weak_hint_never_becomes_candidate():
    result = ModuleTaskResolverService().resolve(
        event={"text_segment": "Hallo"}, detection={}, hints={"ranked_context_hints": [{"kind": "room"}]}
    )
    assert result["confirmed"] is None
    assert result["warnings"] == ["weak_context_hint_only"]
