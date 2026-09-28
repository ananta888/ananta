"""Hub coordination of tasks split because their context exceeds the window (LCTX-007/008).

Three moments, all on the Hub and all through the normal task machinery:

1. **split** (``maybe_split``, called by the autopilot dispatcher before a task is
   handed to a worker): with ``context_strategy.mode = active`` and a decision of
   ``sequential`` or ``map_reduce``, the material is chunked and every planned step
   becomes a Hub task (``ingest_task``) linked to the original by ``source_task_id``
   -- never ``parent_task_id``, which would make the steps wait for the original
   while it waits for them. Dependencies are plain ``depends_on``; map steps are
   chained in waves so at most ``max_parallel`` run at once. The original waits
   (``blocked_by_dependency``) on the final step.
2. **step ready** (``on_dependencies_completed`` from ``reconcile_dependencies``):
   when a step's predecessors are done, their outputs replace the step's
   ``{DEPENDENCY_OUTPUTS}`` placeholder before it becomes ``todo``. A reduce step
   whose merged input is too large is split again by the same decision (bounded
   nesting: hierarchical reduce).
3. **done**: when the final step is completed, the original task takes over its
   output and completes. A failed step fails its dependents and the original
   through the existing dependency rules.

Workers only ever see their own step; there is no worker-to-worker hand-over.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from agent.services.long_context_plan import DEPENDENCY_OUTPUTS, PlannedStep, build_plan
from agent.services.long_context_step_result import step_result

MARKER = "long_context"
SPLIT_STRATEGIES = frozenset({"sequential", "map_reduce"})
EXTERNALIZE_STRATEGIES = frozenset({"compact", "retrieve"})
MATERIAL_FILE = ".ananta/task-material.md"
MAX_NESTING = 2  # a reduce may be split again once or twice, not forever
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SplitResult:
    task_id: str
    strategy: str
    step_ids: tuple[str, ...]
    final_step_id: str

    @property
    def dispatch_now(self) -> bool:
        """The task itself goes on to a worker (externalized) rather than waiting for steps (split)."""
        return not self.step_ids and self.strategy in EXTERNALIZE_STRATEGIES


def material_fit(text: str, service: Any = None, window_tokens: int | None = None):
    """Does ``text`` fit as the material of one request? The central policy decides the room: the effective
    window minus output reserve, safety margin and the fixed request part (tools, system prompt, task);
    ``service.request_overhead_tokens`` (context_strategy config) is that fixed part. ``window_tokens``: the window
    of the runtime that will execute the task (default: the local effective window)."""
    from agent.context_profile import ContextBudgets, effective_window_tokens, request_overhead_tokens
    from agent.context_window import check_fit

    overhead = getattr(service, "request_overhead_tokens", None)
    budgets = ContextBudgets(int(window_tokens or effective_window_tokens()),
                             int(overhead) if overhead is not None else request_overhead_tokens())
    return check_fit(prompt=text, window_tokens=budgets.window,
                     output_reserve_tokens=budgets.window - budgets.available)


def _details(task: Any) -> dict[str, Any]:
    raw = getattr(task, "status_reason_details", None)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _context(task: Any) -> dict[str, Any]:
    raw = getattr(task, "worker_execution_context", None)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _with_waves(steps: list[PlannedStep], parallelism: int) -> list[PlannedStep]:
    """Map step i additionally waits for map step i - parallelism: at most ``parallelism`` run at once."""
    if parallelism <= 0:
        return steps
    maps = [step for step in steps if step.kind == "map"]
    waved = []
    for step in steps:
        if step.kind == "map":
            position = maps.index(step)
            if position >= parallelism:
                step = PlannedStep(step.key, step.kind, step.title, step.description,
                                   step.depends_on + (maps[position - parallelism].key,), step.sources)
        waved.append(step)
    return waved


class LongContextCoordinator:
    def __init__(self, *, ingest_task: Callable[..., Any], update_status: Callable[..., Any],
                 get_task: Callable[[str], Any], record_event: Callable[..., Any] | None = None,
                 strategy_service_factory: Callable[[Any], Any] | None = None) -> None:
        self._ingest = ingest_task
        self._update = update_status
        self._get_task = get_task
        self._record = record_event or (lambda *_args, **_kwargs: None)
        if strategy_service_factory is None:
            from agent.services.context_strategy_service import get_context_strategy_service

            strategy_service_factory = get_context_strategy_service
        self._strategy_factory = strategy_service_factory

    # --- 1. split -------------------------------------------------------------------------------------

    def maybe_split(self, task: Any, *, config: Mapping[str, Any] | None,
                    overflowed: bool = False, window_tokens: int | None = None) -> SplitResult | None:
        """Handle ``task`` when active and its context does not fit; ``None`` otherwise.

        ``overflowed``: a model call already failed on the window (LCTX-009) -- even if the estimate says the
        task fits (the estimate is approximate), it is treated as at least slightly too large."""
        from agent.services.context_chunking import pack_parts, split_ordered
        from agent.services.context_strategy_service import ContextStrategyRequest

        service = self._strategy_factory(config)
        if service.mode != "active":
            return None
        marker = _details(task).get(MARKER) or {}
        nesting = int(marker.get("nesting") or 0)
        role = marker.get("role")
        if role in {"parent", "externalized"} or (role == "step" and marker.get("kind") != "reduce") \
                or nesting >= MAX_NESTING:
            return None  # already handled, a step, or nested deep enough: run as is
        context = _context(task)
        title = str(getattr(task, "title", "") or "").strip()
        material = str(getattr(task, "description", "") or "")
        parts = [(str(p.get("id") or f"part-{i + 1}"), str(p.get("text") or ""))
                 for i, p in enumerate(context.get("context_parts") or []) if isinstance(p, Mapping)]
        size_text = "\n".join([title, material] + [text for _id, text in parts])
        fit = material_fit(size_text, service, window_tokens)
        if fit.fits and overflowed:
            from agent.context_window import ContextFit

            fit = ContextFit(fit.window_tokens, fit.output_reserve_tokens,
                             max(fit.estimated_tokens, int(fit.budget_tokens * 1.2)))
        if fit.fits:
            return None
        input_kind = "parts" if parts else str(context.get("context_input_kind") or "unknown")
        decision = service.decide(ContextStrategyRequest(
            fit=fit, task_kind=str(getattr(task, "task_kind", "") or ""), input_kind=input_kind, parts=len(parts),
            description=f"{title}\n{material[:1500]}"))
        goal = str(context.get("context_goal") or title or material[:500]).strip()
        strategy = decision.strategy
        if strategy == "escalate" and role == "escalated":
            strategy = "sequential"  # a human let it continue: process everything, piece by piece
        if strategy == "escalate":
            return self._escalate(task, decision)
        if strategy in EXTERNALIZE_STRATEGIES and getattr(service, "externalize", True):
            return self._externalize(task, decision, goal, material, parts)
        if strategy in EXTERNALIZE_STRATEGIES:
            decision = service.as_split(decision)
            strategy = decision.strategy
        if strategy not in SPLIT_STRATEGIES:
            return None
        chunk_fill = float(getattr(service, "chunk_fill", 0.6) or 0.6)
        if strategy != decision.strategy:
            from agent.services.context_strategy_service import ContextStrategyDecision

            decision = ContextStrategyDecision(strategy, "escalation_resumed", "rules", decision.fit,
                                               {"chunk_budget_tokens": int(decision.fit.budget_tokens * chunk_fill)})
        chunk_tokens = int(decision.parameters.get("chunk_budget_tokens") or decision.fit.budget_tokens * chunk_fill)
        chunks = pack_parts(parts, chunk_tokens) if parts else split_ordered(material, chunk_tokens)
        from agent.services.long_context_plan import APPROVED_MAX_STEPS, MAX_STEPS

        max_steps = APPROVED_MAX_STEPS if role == "escalated" else MAX_STEPS
        steps = _with_waves(build_plan(decision.strategy, goal, chunks, title=title, max_steps=max_steps),
                            int(decision.parameters.get("parallelism") or 0))
        return self._materialize(task, decision, steps, nesting)

    def _materialize(self, task: Any, decision: Any, steps: list[PlannedStep], nesting: int) -> SplitResult:
        task_id = str(task.id)
        ids = {step.key: f"{task_id}-lc-{step.key}" for step in steps}
        step_context = {k: v for k, v in _context(task).items() if k not in {"context_parts", "context_goal"}}
        for step in steps:
            depends_on = [ids[key] for key in step.depends_on]
            self._ingest(
                task_id=ids[step.key], status="blocked_by_dependency" if depends_on else "todo",
                title=step.title, description=step.description,
                priority=str(getattr(task, "priority", "") or "medium"), created_by="hub-long-context",
                source="long_context", team_id=getattr(task, "team_id", None),
                event_type="long_context_step_created",
                extra_fields={
                    "goal_id": getattr(task, "goal_id", None), "source_task_id": task_id,
                    "derivation_reason": f"long_context_{decision.strategy}",
                    "derivation_depth": int(getattr(task, "derivation_depth", 0) or 0) + 1,
                    "depends_on": depends_on, "task_kind": getattr(task, "task_kind", None),
                    # a merge step that is itself too large is merged piece by piece (hierarchical reduce)
                    "worker_execution_context": {**step_context, "context_input_kind": "ordered"}
                    if step.kind in {"reduce", "consolidate"} else step_context,
                    "status_reason_details": {MARKER: {
                        "role": "step", "parent_task_id": task_id, "key": step.key, "kind": step.kind,
                        "nesting": nesting + (1 if step.kind == "reduce" else 0), "sources": list(step.sources)}},
                })
        final_id = ids[steps[-1].key]
        details = {**_details(task), MARKER: {
            "role": "parent", "strategy": decision.strategy, "steps": list(ids.values()), "final": final_id,
            "decision": decision.to_mapping(), "nesting": nesting}}
        self._update(task_id, "blocked_by_dependency", depends_on=[final_id], status_reason_details=details,
                     assigned_agent_url=None, event_type="long_context_split",
                     event_details={"strategy": decision.strategy, "steps": len(ids)})
        self._record("long_context_split", task_id=task_id, strategy=decision.strategy, steps=len(ids),
                     ratio=round(decision.fit.ratio, 2))
        return SplitResult(task_id, decision.strategy, tuple(ids.values()), final_id)

    def _externalize(self, task: Any, decision: Any, goal: str, material: str,
                     parts: list[tuple[str, str]]) -> SplitResult:
        """compact / retrieve: the material moves into a workspace file the worker reads section by section."""
        from agent.services.context_chunking import split_ordered

        text = "\n\n".join([material] + [f"### {part_id}\n{body}" for part_id, body in parts]).strip()
        outline, line = [], 1
        for chunk in split_ordered(text, 3000):
            lines = chunk.text.count("\n") + 1
            # the documents/parts a section holds (their headings), else its first line
            headings = [row[2:].strip() for row in chunk.text.splitlines() if row.startswith(("# ", "### "))]
            first = next((row.strip() for row in chunk.text.splitlines() if row.strip()), "")[:100]
            label = ", ".join(heading.lstrip("# ")[:80] for heading in headings[:4]) or first
            if len(headings) > 4:
                label += f" (+{len(headings) - 4})"
            outline.append(f"- Abschnitt {chunk.index + 1}, Zeilen {line}–{line + lines - 1}: {label}")
            line += lines + 1  # the blank line between chunks
        reading = ("Lies es vollständig, Abschnitt für Abschnitt (`repo.read_file_range`), und halte das Wesentliche "
                   "fest, bevor du antwortest." if decision.strategy == "compact" else
                   "Suche zuerst gezielt nach den Begriffen der Aufgabe (`repo.grep` in dieser Datei, auch "
                   "Varianten und Umgebungsvariablen-Namen), lies dann die Fundstellen und nur die relevanten "
                   "Abschnitte (`repo.read_file_range` mit den Zeilen aus der Gliederung). Antworte nur mit dem, "
                   "was im Material steht, und nenne die Quelle (Dokument).")
        size = f"~{decision.fit.estimated_tokens} Token, {decision.fit.ratio:.1f}× das Kontextfenster"
        description = (f"Aufgabe: {goal}\n\nDas Material zu dieser Aufgabe ist umfangreich ({size}) und liegt in "
                       f"`{MATERIAL_FILE}`. "
                       f"{reading}\n\nGliederung:\n" + "\n".join(outline[:200]))
        context = {**{k: v for k, v in _context(task).items() if k != "context_parts"}, "context_material": text}
        details = {**_details(task), MARKER: {"role": "externalized", "strategy": decision.strategy,
                                              "decision": decision.to_mapping(), "sections": len(outline)}}
        self._update(str(task.id), str(getattr(task, "status", "") or "todo"), description=description,
                     worker_execution_context=context, status_reason_details=details,
                     event_type="long_context_externalized",
                     event_details={"strategy": decision.strategy, "sections": len(outline)})
        self._record("long_context_externalized", task_id=str(task.id), strategy=decision.strategy,
                     sections=len(outline), ratio=round(decision.fit.ratio, 2))
        return SplitResult(str(task.id), decision.strategy, (), "")

    def _escalate(self, task: Any, decision: Any) -> SplitResult:
        """Too large to handle automatically: pause for a human; resuming processes it piece by piece."""
        details = {**_details(task), MARKER: {"role": "escalated", "decision": decision.to_mapping()}}
        self._update(str(task.id), "paused", status_reason_details=details,
                     status_reason_code="context_too_large_needs_decision", event_type="long_context_escalated",
                     event_details={"ratio": round(decision.fit.ratio, 2)})
        self._record("long_context_escalated", task_id=str(task.id), ratio=round(decision.fit.ratio, 2))
        return SplitResult(str(task.id), "escalate", (), "")

    # --- 2. and 3. ------------------------------------------------------------------------------------

    def on_dependencies_completed(self, task: Any, dependency_ids: list[str]) -> dict[str, Any] | None:
        """Handle a split task or a step whose dependencies are all done; ``None`` leaves the normal path."""
        marker = _details(task).get(MARKER) or {}
        role = marker.get("role")
        if role == "parent":
            final = self._get_task(str(marker.get("final") or ""))
            output = step_result(final) if final is not None else ""
            # like the recovery finalizer: the Hub completes a task whose result its steps produced. The state
            # machine knows no blocked -> completed edge, so this one audited transition is forced.
            self._update(str(task.id), "completed", last_output=output, last_exit_code=0, force=True,
                         event_type="long_context_completed",
                         event_details={"strategy": marker.get("strategy"), "final_step": marker.get("final")})
            self._record("long_context_completed", task_id=str(task.id), strategy=marker.get("strategy"))
            return {"task_id": task.id, "event_type": "long_context_completed", "depends_on": dependency_ids,
                    "reason": "final_step_completed"}
        description = str(getattr(task, "description", "") or "")
        if role != "step" or DEPENDENCY_OUTPUTS not in description:
            return None
        outputs = []
        for dep_id in dependency_ids:
            dep = self._get_task(dep_id)
            if dep is None:
                continue
            dep_marker = _details(dep).get(MARKER) or {}
            if dep_marker.get("parent_task_id") != marker.get("parent_task_id"):
                continue  # a wave dependency or a foreign task: not an input of this step
            outputs.append(f"### {getattr(dep, 'title', dep_id)}\n{step_result(dep)}")
        self._update(str(task.id), "todo", description=description.replace(DEPENDENCY_OUTPUTS, "\n\n".join(outputs)),
                     event_type="long_context_step_ready", event_details={"inputs": len(outputs)})
        return {"task_id": task.id, "event_type": "long_context_step_ready", "depends_on": dependency_ids,
                "reason": "dependency_outputs_inserted"}


def get_long_context_coordinator() -> LongContextCoordinator:
    from agent.repository import task_repo
    from agent.services.product_event_service import record_product_event
    from agent.services.task_queue_service import get_task_queue_service
    from agent.services.task_runtime_service import update_local_task_status

    def record(name: str, **details: Any) -> None:
        try:
            record_product_event(name, actor="long_context_coordinator", details=details)
        except Exception:  # noqa: BLE001 -- events never change the flow
            _log.debug("long context event failed", exc_info=True)

    return LongContextCoordinator(ingest_task=get_task_queue_service().ingest_task,
                                  update_status=update_local_task_status, get_task=task_repo.get_by_id,
                                  record_event=record)
