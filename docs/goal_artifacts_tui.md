# Goal Artifacts im Operator TUI

Diese Ansicht zeigt für ein aktives Goal drei Ebenen: **freigegebene Quellen**, **tatsächlich genutzte Quellen** und **erzeugte Output-Artefakte**.

## Kommandos

- `:goal use <goal-id>` setzt das aktive Goal.
- `:goal artifacts` lädt die Gesamtansicht (Grants, Usages, Outputs).
- `:goal sources candidates` zeigt freigabefähige Quellartefakte.
- `:goal source grant <artifact-ref> --usage use_as_context` erstellt eine Freigabe.
- `:goal source revoke <grant-id>` widerruft eine Freigabe.
- `:goal source detail <grant-id>` zeigt Sicherheitskontext (Boundary, Sensitivity, Policy).
- `:artifact provenance <output-artifact-id>` zeigt Ausführung/Inputs zum Output.
- `:artifact prompt <output-artifact-id>` zeigt Prompt-Template- und Prompt-Hash-Referenzen.
- `:artifact config <output-artifact-id>` zeigt Config-Snapshot-Referenzen.

## Freigegeben vs. genutzt

- **Freigegeben (Grant):** Ein Artefakt darf genutzt werden.
- **Genutzt (Usage):** Ein Worker hat die Quelle in einer konkreten Task wirklich verwendet.

Ein Grant ohne Usage bedeutet: erlaubt, aber (noch) nicht genutzt. Ein Usage verweist immer auf einen existierenden Grant.

## Output-Provenance

Output-Artefakte enthalten Referenzen auf:

- `task_id`, `worker_id`, `execution_id`
- `input_usage_refs` (welche Quellen konkret einflossen)
- `provenance_id` für Details (Runtime/Model/Config/Prompt)

Damit ist nachvollziehbar, wie ein Ergebnis entstand, ohne Rohdaten ungefragt offenzulegen.

## Security-Hinweise

- Boundary und Sensitivity kommen aus der Freigabe und bleiben sichtbar.
- Prompt- und Config-Details werden als Referenzen/Hashes angezeigt.
- Rohprompt-Zugriff ist policy-gesteuert und standardmäßig blockiert.

## Kurzbeispiel Goal-Flow

1. `:goal use goal-42`
2. `:goal sources candidates`
3. `:goal source grant sources:keycloak:snap_1 --usage use_as_context`
4. Worker erzeugt Output-Artefakt
5. `:goal artifacts`
6. `:artifact provenance out-42`
