"""Plans for tasks beyond the context window: steps and their dependencies (LCTX-007/008).

Pure planning, no persistence: the Hub materializes the steps as child tasks.

- ``sequential``: chunk 1 -> chunk 2 -> ... -> consolidate. Each chunk step gets the
  previous step's output ("carried notes") and returns updated notes; the final step
  turns the last notes into the result. Order and cross-chunk context are kept.
- ``map_reduce``: all map steps are independent (parallel), then one reduce step
  merges their results. If the merged results are too large again, the reduce step
  itself goes through the Hub's decision point (hierarchical reduce).

Dependency outputs are inserted at run time where ``{DEPENDENCY_OUTPUTS}`` stands.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from agent.services.context_chunking import Chunk

DEPENDENCY_OUTPUTS = "{DEPENDENCY_OUTPUTS}"
MAX_STEPS = 64
NOTES_HEADING = "## Zwischenstand"
PARTIAL_HEADING = "## Teilergebnis"


class LongContextPlanError(ValueError):
    pass


@dataclass(frozen=True)
class PlannedStep:
    key: str
    kind: str  # chunk | consolidate | map | reduce
    title: str
    description: str
    depends_on: tuple[str, ...] = ()
    sources: tuple[str, ...] = field(default_factory=tuple)


def _check(chunks: Sequence[Chunk]) -> None:
    if len(chunks) < 2:
        raise LongContextPlanError("long_context_plan_needs_two_chunks")
    if len(chunks) + 1 > MAX_STEPS:
        raise LongContextPlanError("long_context_plan_too_many_steps")  # escalate instead


def sequential_plan(goal: str, chunks: Sequence[Chunk], *, title: str = "") -> list[PlannedStep]:
    _check(chunks)
    label = (title or goal).strip()[:80]
    steps: list[PlannedStep] = []
    total = len(chunks)
    for chunk in chunks:
        number = chunk.index + 1
        carried = (f"Bisheriger Zwischenstand aus Teil 1 bis {number - 1}:\n{DEPENDENCY_OUTPUTS}\n\n"
                   if number > 1 else "")
        steps.append(PlannedStep(
            key=f"chunk-{number}", kind="chunk", title=f"{label} – Teil {number}/{total}",
            description=(
                f"Aufgabe (wird in {total} Teilen nacheinander bearbeitet): {goal}\n\n{carried}"
                f"Material, Teil {number} von {total}:\n{chunk.text}\n\n"
                f"Arbeite nur mit diesem Teil und dem Zwischenstand. Antworte mit '{NOTES_HEADING}': dem "
                "aktualisierten Zwischenstand für die Gesamtaufgabe (Erkenntnisse, Entscheidungen, offene Punkte, "
                "Belege mit Fundstelle). Übernimm Wichtiges aus dem bisherigen Zwischenstand, er ersetzt ihn."),
            depends_on=(f"chunk-{number - 1}",) if number > 1 else (), sources=chunk.sources))
    steps.append(PlannedStep(
        key="consolidate", kind="consolidate", title=f"{label} – Ergebnis",
        description=(f"Aufgabe: {goal}\n\nDas Material wurde in {total} Teilen nacheinander bearbeitet. "
                     f"Finaler Zwischenstand:\n{DEPENDENCY_OUTPUTS}\n\n"
                     "Erstelle daraus das vollständige Ergebnis der Aufgabe."),
        depends_on=(f"chunk-{total}",)))
    return steps


def map_reduce_plan(goal: str, chunks: Sequence[Chunk], *, title: str = "") -> list[PlannedStep]:
    _check(chunks)
    label = (title or goal).strip()[:80]
    total = len(chunks)
    steps = [PlannedStep(
        key=f"map-{chunk.index + 1}", kind="map", title=f"{label} – Teil {chunk.index + 1}/{total}",
        description=(f"Aufgabe (wird in {total} unabhängigen Teilen bearbeitet): {goal}\n\n"
                     f"Material, Teil {chunk.index + 1} von {total}:\n{chunk.text}\n\n"
                     f"Bearbeite nur diesen Teil. Antworte mit '{PARTIAL_HEADING}': dem Ergebnis für diesen Teil, "
                     "mit Fundstellen, so dass es sich mit den anderen Teilen zusammenführen lässt."),
        sources=chunk.sources) for chunk in chunks]
    steps.append(PlannedStep(
        key="reduce", kind="reduce", title=f"{label} – Zusammenführung",
        description=(f"Aufgabe: {goal}\n\nDas Material wurde in {total} unabhängigen Teilen bearbeitet. "
                     f"Teilergebnisse:\n{DEPENDENCY_OUTPUTS}\n\n"
                     "Führe sie zum vollständigen Ergebnis der Aufgabe zusammen; löse Widersprüche und Doppeltes auf."),
        depends_on=tuple(step.key for step in steps)))
    return steps


def build_plan(strategy: str, goal: str, chunks: Sequence[Chunk], *, title: str = "") -> list[PlannedStep]:
    if strategy == "sequential":
        return sequential_plan(goal, chunks, title=title)
    if strategy == "map_reduce":
        return map_reduce_plan(goal, chunks, title=title)
    raise LongContextPlanError(f"long_context_plan_strategy_unsupported:{strategy}")
