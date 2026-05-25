# Context Release Gates

## Ziel

Jede Artefaktuebergabe an Worker/LLMs laeuft durch ein Release-Gate. Ohne Gate-Entscheidung kein Kontextfluss.

## Pflichtpruefungen

- `task_id` und Zielruntime
- Ziel-Worker/Provider/LLM-Scope (lokal vs cloud)
- Artefaktklasse
- Grant + Ablaufzeit + Revocation-Status
- Aktionstyp (`provide_to_worker` vs `provide_to_remote_llm`)

## Entscheidungslogik

- Default-Deny.
- `provide_to_remote_llm` niemals implizit.
- Ablehnung muss fuer Nutzer sichtbar sein und im Audit erscheinen.

## Runtime-Grenze

- Lokale Worker und Cloud-Worker koennen unterschiedliche Policy-Pfade nutzen.
- `local_only` darf nur in lokale Runtimes released werden.

