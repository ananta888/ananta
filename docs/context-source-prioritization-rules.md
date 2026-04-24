# Context Source Prioritization Rules

Diese Regeln beschreiben, wie der Context-Manager bei begrenztem Budget Chunks priorisiert und kompakt haelt, ohne Provenance zu verlieren.

## Version

- `source-priority-rules-v1` (Retrieval/Fusion)
- `source-priority-reservation-v1` (Budget-Reservierung im Context-Bundle)
- `priority-budget-compaction-v1` (Compaction/Drop-Reasons)

## Prioritaetsstufen

1. **critical**: `record_kind` in `{policy,constraint,security_note,approval,contract}`
2. **high**: direkte Task-Relation (`same_task`, `direct_parent`, `direct_child`) oder `source_type` in `{task_memory,artifact}`
3. **medium**: `source_type` in `{goal_memory,result_memory,wiki,kb}`
4. **low**: verbleibender Kontext

## Budget-Modell

- Das Gesamtbudget wird in Sektionen aufgeteilt (`sectional_v2`).
- Die Retrieval-Sektion reserviert Tokens pro Prioritaet.
- Nicht genutzte Prioritaets-Reserven duerfen in einer zweiten Runde nachgenutzt werden.

## Observability

Im Bundle sind folgende Diagnostik-Felder relevant:

- `budget.priority_reservations.tokens_by_priority`
- `compaction.dropped_reasons`
- `compaction.dropped_chunks[]` (mit `source`, `record_kind`, `source_type`, `chunk_id`)
- `context_policy.source_prioritization_rules`
- `selection_trace.compaction`
