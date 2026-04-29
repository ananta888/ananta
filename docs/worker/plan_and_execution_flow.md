# Worker Plan and Execution Flow

## Plan-Artefakt

- Worker erzeugt ein task-lokales Plan-Artefakt (`worker_plan_artifact.v1`).
- Schritte haben explizite Abhängigkeiten, Tool-Bedarf und erwartete Artefakte.
- Zustände sind maschinenlesbar und deterministisch.

## Planner State Machine

- Zustände: `draft`, `ready`, `executing`, `verifying`, `replanning`, `complete`, `failed`
- Ungültige Transitionen werden explizit abgewiesen.
- Zustand kann per Checkpoint persistiert und wieder aufgenommen werden.

## Scheduler und Budgets

- Scheduler führt nur `ready`-Schritte mit erfüllten Dependencies aus.
- Step-Budgets sind getrennt für Tokens, Laufzeit, Kommandos und Patch-Versuche.
- Budget-Exhaustion liefert explizites `stop_reason`.

## Replan und Audit

- Trigger: Verifikation fehlgeschlagen, Artefakt fehlt, Policy denied, Budget exhausted
- Replan-Versuche sind begrenzt und separat gezählt.
- Jeder Replan erzeugt maschinenlesbaren Plan-Diff (`added`, `removed`, `reprioritized`).

## Checkpoint/Resume

- Checkpoint enthält Planner-State, erledigte/offene Schritte und Budget-Zähler.
- Resume setzt beim letzten konsistenten Stand fort.
- Policy-Snapshot-Referenzen bleiben nachvollziehbar.

