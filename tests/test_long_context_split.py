"""LCTX-007/008: tasks beyond the context window are split into Hub step tasks and completed from them."""

from __future__ import annotations

import pytest

from agent.services.context_chunking import pack_parts, split_ordered
from agent.services.long_context_plan import DEPENDENCY_OUTPUTS, LongContextPlanError, build_plan

pytestmark = pytest.mark.timeout(60)
ACTIVE = {"mode": "active", "ask_decision_provider": False, "request_overhead_tokens": 0}  # sizes below: material only
EXTERNALIZE = {**ACTIVE, "externalize": True}  # tasks run through an iteratively reading worker


def _long_text(paragraphs=200, words=300):
    return "\n\n".join(f"Abschnitt {i}: " + "wort " * words for i in range(paragraphs))


# --- chunking and plans (pure) -------------------------------------------------------------------------


def test_ordered_splitting_keeps_everything_within_the_budget():
    text = _long_text(40)
    chunks = split_ordered(text, 2000)
    assert len(chunks) > 1 and all(chunk.tokens <= 2000 for chunk in chunks)
    squash = lambda value: value.replace("\n", "").replace(" ", "")  # noqa: E731
    assert "".join(squash(chunk.text) for chunk in chunks) == squash(text)  # nothing lost, order kept
    assert split_ordered("x" * 50_000, 1000)[0].tokens <= 1000  # even without any boundary


def test_parts_are_packed_and_oversize_parts_split_with_their_origin():
    parts = [(f"f{i}.py", "a = 1\n" * (40 if i != 3 else 4000)) for i in range(6)]
    chunks = pack_parts(parts, 2000)
    assert all(chunk.tokens <= 2000 for chunk in chunks)
    assert {source for chunk in chunks for source in chunk.sources} == {f"f{i}.py" for i in range(6)}
    assert sum(1 for chunk in chunks if chunk.sources == ("f3.py",)) >= 2  # the big file became several chunks


def test_plans_chain_sequential_steps_and_fan_in_map_steps():
    chunks = split_ordered(_long_text(20), 1500)
    sequential = build_plan("sequential", "Fasse zusammen", chunks)
    assert [s.depends_on for s in sequential[:3]] == [(), ("chunk-1",), ("chunk-2",)]
    assert sequential[-1].key == "consolidate" and DEPENDENCY_OUTPUTS in sequential[-1].description
    assert DEPENDENCY_OUTPUTS not in sequential[0].description and DEPENDENCY_OUTPUTS in sequential[1].description
    mapped = build_plan("map_reduce", "Prüfe alle Dateien", chunks)
    assert all(not s.depends_on for s in mapped[:-1]) and set(mapped[-1].depends_on) == {s.key for s in mapped[:-1]}
    assert all("`final_answer`" in s.description for s in sequential + mapped)
    with pytest.raises(LongContextPlanError):
        build_plan("sequential", "x", chunks[:1])


# --- split, run, complete (Hub task machinery, test DB) ------------------------------------------------


def _coordinator():
    from agent.services.long_context_coordinator import get_long_context_coordinator

    return get_long_context_coordinator()


def _save(task_id, **fields):
    from agent.db_models import TaskDB
    from agent.services.repository_registry import get_repository_registry

    get_repository_registry().task_repo.save(TaskDB(id=task_id, **fields))


def _get(task_id):
    from agent.services.repository_registry import get_repository_registry

    return get_repository_registry().task_repo.get_by_id(task_id)


def _reconcile(ids):
    from agent.services.task_queue_service import TaskQueueService

    return TaskQueueService().reconcile_dependencies(tasks=[_get(i) for i in ids],
                                                     dependency_resolver=lambda t: list(t.depends_on or []))


def _complete(task_id, output):
    from agent.services.task_runtime_service import update_local_task_status

    update_local_task_status(task_id, "completed", last_output=output, force=True)


def test_a_long_ordered_task_runs_sequentially_with_carried_notes(app):
    with app.app_context():
        _save("lc-doc", status="todo", title="Protokoll zusammenfassen", description=_long_text(),
              worker_execution_context={"context_input_kind": "ordered"})
        split = _coordinator().maybe_split(_get("lc-doc"), config=ACTIVE)
        assert split.strategy == "sequential" and len(split.step_ids) >= 4
        parent = _get("lc-doc")
        assert parent.status == "blocked_by_dependency" and parent.depends_on == [split.final_step_id]
        assert parent.status_reason_details["long_context"]["role"] == "parent"
        steps = [_get(i) for i in split.step_ids]
        assert steps[0].status == "todo" and all(s.status == "blocked_by_dependency" for s in steps[1:])
        assert all(s.source_task_id == "lc-doc" and not s.parent_task_id for s in steps)  # no deadlock
        assert all(s.derivation_depth == 1 for s in steps)
        assert all(s.status_reason_details["long_context"]["role"] == "step" for s in steps)
        # run the chain: each step sees the notes of the one before
        for index, step_id in enumerate(split.step_ids[:-1]):
            _complete(step_id, f"## Zwischenstand\nNotiz nach Teil {index + 1}")
            _reconcile(list(split.step_ids) + ["lc-doc"])
            following = _get(split.step_ids[index + 1])
            assert following.status == "todo" and f"Notiz nach Teil {index + 1}" in following.description
            assert DEPENDENCY_OUTPUTS not in following.description
        assert _get("lc-doc").status == "blocked_by_dependency"
        _complete(split.final_step_id, "Endergebnis der Zusammenfassung")
        transitions = _reconcile(["lc-doc"])
        assert transitions[0]["event_type"] == "long_context_completed"
        parent = _get("lc-doc")
        assert parent.status == "completed" and parent.last_output == "Endergebnis der Zusammenfassung"


def test_independent_parts_map_in_waves_and_reduce(app):
    parts = [{"id": f"protokoll-{i}.md", "text": "eintrag " * 3000} for i in range(12)]
    with app.app_context():
        _save("lc-parts", status="todo", title="Protokolle auswerten", description="Werte alle Protokolle aus.",
              worker_execution_context={"context_parts": parts})
        split = _coordinator().maybe_split(_get("lc-parts"), config={**ACTIVE, "max_parallel": 2})
        assert split.strategy == "map_reduce"
        maps = [_get(i) for i in split.step_ids[:-1]]
        reduce = _get(split.final_step_id)
        assert [m.status for m in maps[:2]] == ["todo", "todo"]  # at most two at once
        assert all(m.status == "blocked_by_dependency" for m in maps[2:])
        assert set(reduce.depends_on) >= {m.id for m in maps}
        assert "context_parts" not in (maps[0].worker_execution_context or {})  # a step carries only its chunk
        for index, step_id in enumerate(split.step_ids[:-1]):
            _complete(step_id, f"## Teilergebnis {index + 1}")
            _reconcile(list(split.step_ids))
        reduce = _get(split.final_step_id)
        assert reduce.status == "todo" and all(f"Teilergebnis {i + 1}" in reduce.description for i in range(len(maps)))


def test_a_failed_step_fails_the_split_task(app):
    with app.app_context():
        _save("lc-fail", status="todo", title="Lang", description=_long_text(),
              worker_execution_context={"context_input_kind": "ordered"})
        split = _coordinator().maybe_split(_get("lc-fail"), config=ACTIVE)
        from agent.services.task_runtime_service import update_local_task_status

        update_local_task_status(split.step_ids[0], "failed", force=True)
        for _ in range(len(split.step_ids) + 1):
            _reconcile(list(split.step_ids) + ["lc-fail"])
        assert _get("lc-fail").status == "failed"


def test_nothing_happens_in_shadow_mode_for_fitting_tasks_or_for_steps(app):
    with app.app_context():
        _save("lc-small", status="todo", title="Klein", description="kurz")
        _save("lc-shadow", status="todo", title="Lang", description=_long_text())
        coordinator = _coordinator()
        assert coordinator.maybe_split(_get("lc-small"), config=ACTIVE) is None
        assert coordinator.maybe_split(_get("lc-shadow"), config={"mode": "shadow"}) is None
        assert _get("lc-shadow").status == "todo"
        split = coordinator.maybe_split(_get("lc-shadow"), config=ACTIVE)
        assert coordinator.maybe_split(_get(split.step_ids[0]), config=ACTIVE) is None  # a step is not split again
        assert coordinator.maybe_split(_get("lc-shadow"), config=ACTIVE) is None  # nor the split task


def test_the_dispatcher_splits_only_in_active_mode(app, monkeypatch):
    from types import SimpleNamespace

    from agent.routes.tasks import autopilot_task_dispatcher as dispatcher

    calls = []
    monkeypatch.setattr("agent.services.long_context_coordinator.get_long_context_coordinator",
                        lambda: SimpleNamespace(maybe_split=lambda task, config: calls.append(config) or "split"))
    fake_app = SimpleNamespace(config={"AGENT_CONFIG": {"context_strategy": {"mode": "shadow"}}})
    assert dispatcher._split_if_beyond_context(SimpleNamespace(id="t"), app=fake_app) is None and calls == []
    fake_app.config["AGENT_CONFIG"]["context_strategy"]["mode"] = "active"
    assert dispatcher._split_if_beyond_context(SimpleNamespace(id="t"), app=fake_app) == "split"


# --- compact / retrieve: material as a workspace file; escalate: pause (LCTX-005/006) ------------------


def test_a_slightly_oversized_task_is_externalized_and_dispatched(app):
    with app.app_context():
        material = "\n".join(f"Zeile {i}: " + "inhalt " * 20 for i in range(1100))  # ~1.2x the budget
        _save("lc-compact", status="todo", title="Bericht prüfen", description=material,
              worker_execution_context={"context_input_kind": "ordered"})
        result = _coordinator().maybe_split(_get("lc-compact"), config=EXTERNALIZE)
        assert result.strategy == "compact" and result.dispatch_now and result.step_ids == ()
        task = _get("lc-compact")
        assert task.status == "todo" and ".ananta/task-material.md" in task.description
        assert "Gliederung:" in task.description and "Zeilen 1–" in task.description
        assert task.worker_execution_context["context_material"].startswith("Zeile 0:")
        from agent.context_window import check_fit

        assert check_fit(prompt=task.description).fits  # the task now fits the window
        assert _coordinator().maybe_split(task, config=EXTERNALIZE) is None  # handled once


def test_a_corpus_is_externalized_for_selective_reading(app):
    with app.app_context():
        _save("lc-corpus", status="todo", title="Wo wird X konfiguriert?", description="doku " * 60_000,
              worker_execution_context={"context_input_kind": "corpus"})
        result = _coordinator().maybe_split(_get("lc-corpus"), config=EXTERNALIZE)
        assert result.strategy == "retrieve" and "relevant" in _get("lc-corpus").description


def test_without_externalizing_compact_and_retrieve_are_carried_out_as_splits(app):
    with app.app_context():
        material = "\n".join(f"Zeile {i}: " + "inhalt " * 20 for i in range(1100))  # ~1.2x the budget
        _save("lc-compact-split", status="todo", title="Bericht prüfen", description=material,
              worker_execution_context={"context_input_kind": "ordered"})
        _save("lc-corpus-split", status="todo", title="Wo wird X konfiguriert?", description="doku " * 60_000,
              worker_execution_context={"context_input_kind": "corpus"})
        compact = _coordinator().maybe_split(_get("lc-compact-split"), config=ACTIVE)
        corpus = _coordinator().maybe_split(_get("lc-corpus-split"), config=ACTIVE)
        assert compact.strategy == "sequential" and not compact.dispatch_now and len(compact.step_ids) >= 3
        assert compact.final_step_id.endswith("-lc-consolidate")
        assert corpus.strategy == "map_reduce" and corpus.final_step_id.endswith("-lc-reduce")
        assert ".ananta/task-material.md" not in _get("lc-compact-split").description
        record = _get("lc-corpus-split").status_reason_details["long_context"]
        assert record["decision"]["reason"].startswith("retrieve_as_split:")


def test_the_fixed_request_part_counts_against_the_window(app):
    with app.app_context():
        _save("lc-overhead", status="todo", title="Knapp", description="text " * 20_000,  # ~25k: fits alone
              worker_execution_context={"context_input_kind": "ordered"})
        assert _coordinator().maybe_split(_get("lc-overhead"), config=ACTIVE) is None
        result = _coordinator().maybe_split(_get("lc-overhead"), config={**ACTIVE, "request_overhead_tokens": 12000})
        assert result.strategy == "sequential"  # 25k material + 12k tools/system prompt exceed 32k
        decision = _get("lc-overhead").status_reason_details["long_context"]["decision"]
        from agent.context_profile import ContextBudgets

        budgets = ContextBudgets(32768, 12000)  # output reserve + safety margin + the fixed request part
        assert decision["fit"]["output_reserve_tokens"] == 32768 - budgets.available


def test_an_enormous_task_is_paused_and_a_resumed_one_processed_piece_by_piece(app):
    with app.app_context():
        _save("lc-huge", status="todo", title="Alles", description="x " * 2_700_000,  # ~45x the budget
              worker_execution_context={"context_input_kind": "ordered"})
        result = _coordinator().maybe_split(_get("lc-huge"), config=ACTIVE)
        assert result.strategy == "escalate" and not result.dispatch_now
        task = _get("lc-huge")
        assert task.status == "paused" and task.status_reason_code == "context_too_large_needs_decision"
        from agent.services.task_runtime_service import update_local_task_status

        update_local_task_status("lc-huge", "todo", force=True)  # a human resumes it
        result = _coordinator().maybe_split(_get("lc-huge"), config=ACTIVE)
        assert result.strategy == "sequential" and len(result.step_ids) > 2


def test_the_worker_workspace_gets_the_material_file(tmp_path):
    from agent.services.worker_workspace_service import WorkerWorkspaceContext, WorkerWorkspaceService

    workspace = tmp_path / "ws"
    for directory in (workspace, workspace / "artifacts", workspace / "rag_helper"):
        directory.mkdir(parents=True, exist_ok=True)
    context = WorkerWorkspaceContext(workspace_dir=workspace, artifacts_dir=workspace / "artifacts",
                                     rag_helper_dir=workspace / "rag_helper", artifact_sync={})
    manifest = WorkerWorkspaceService().prepare_opencode_context_files(
        task={"id": "T", "title": "t", "description": "d",
              "worker_execution_context": {"context_material": "Material Zeile 1\nZeile 2"}},
        workspace_context=context, base_prompt="p", system_prompt=None, context_text=None,
        expected_output_schema=None, tool_definitions=None, research_context=None)
    assert manifest["task_material_path"] == ".ananta/task-material.md"
    assert (workspace / ".ananta/task-material.md").read_text(encoding="utf-8").startswith("Material Zeile 1")
    assert ".ananta/task-material.md" in (workspace / manifest["context_index_path"]).read_text(encoding="utf-8")


# --- runtime overflow and the former hard cuts (LCTX-009) ---------------------------------------------


def test_overflow_failures_are_recognized():
    from agent.routes.tasks.autopilot_task_dispatcher import _is_context_overflow

    assert _is_context_overflow([{"failure_type": "preflight_context_limit"}])
    assert _is_context_overflow([{"failure_type": "forward_error",
                                  "reason": "400: This model's maximum context length is 32768 tokens"}])
    assert _is_context_overflow([{"reason": "cannot truncate prompt with n_keep >= n_ctx"}])
    assert _is_context_overflow([{"reason": "token_budget_exceeded: prompt ~40000 tokens exceeds limit 32768"}])
    assert not _is_context_overflow([{"failure_type": "invalid_proposal", "reason": "no command"}])


def test_a_real_overflow_externalizes_even_what_the_estimate_thought_fits(app):
    with app.app_context():
        _save("lc-overflow", status="todo", title="Knapp", description="text " * 20_000,  # estimate: fits
              worker_execution_context={"context_input_kind": "ordered"})
        coordinator = _coordinator()
        assert coordinator.maybe_split(_get("lc-overflow"), config=ACTIVE) is None
        result = coordinator.maybe_split(_get("lc-overflow"), config=EXTERNALIZE, overflowed=True)
        assert result.strategy == "compact" and result.dispatch_now
        assert ".ananta/task-material.md" in _get("lc-overflow").description


def test_the_dispatcher_handles_overflow_only_in_active_mode(monkeypatch):
    from types import SimpleNamespace

    from agent.routes.tasks import autopilot_task_dispatcher as dispatcher

    seen = []
    monkeypatch.setattr("agent.services.long_context_coordinator.get_long_context_coordinator",
                        lambda: SimpleNamespace(maybe_split=lambda task, config, overflowed: seen.append(overflowed)
                                                or "handled"))
    app = SimpleNamespace(config={"AGENT_CONFIG": {"context_strategy": {"mode": "shadow"}}})
    assert dispatcher._handle_context_overflow(SimpleNamespace(id="t"), app=app) is None
    app.config["AGENT_CONFIG"]["context_strategy"]["mode"] = "active"
    assert dispatcher._handle_context_overflow(SimpleNamespace(id="t"), app=app) == "handled" and seen == [True]


def test_planning_segments_grow_with_the_context_instead_of_cutting():
    from agent.services.planning_strategies import LLMPlanningStrategy

    policy = {"segmented_planning_enabled": True, "segment_context_chars": 8000, "max_segments": 3}
    assert LLMPlanningStrategy.segmentation(policy, 5_000) == (8000, 3)
    assert LLMPlanningStrategy.segmentation(policy, 40_000) == (8000, 5)
    assert LLMPlanningStrategy.segmentation(policy, 400_000) == (8000, 8)  # beyond 8 segments: cut, recorded
    assert LLMPlanningStrategy.segmentation({**policy, "segmented_planning_enabled": False}, 40_000) == (8000, 3)
    from agent.config_defaults import build_default_agent_config

    assert build_default_agent_config()["planning_policy"]["segment_context_chars"] == 8000


def test_recovery_context_uses_a_quarter_of_the_window():
    from agent.services.task_recovery_planning_service import _recovery_context_chars

    assert _recovery_context_chars() == 32768 * 4 // 4
