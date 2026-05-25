# Artifact Sharing Workflow

## Prozess

1. **Request**  
   Antrag auf Aktion (`download_encrypted`, `decrypt`, `share`, `provide_to_worker`, `provide_to_remote_llm`).
2. **Policy Check**  
   Deterministische Pruefung von Klassifikation, Subjekt, Ziel, Task, Runtime.
3. **Approval**  
   Optionaler Human-Approval fuer sensitive Klassen/Ziele.
4. **Grant**  
   Erzeugung eines zeitlich begrenzten Grants mit minimalen Rechten.
5. **Audit**  
   Lueckenlose Events fuer Antrag, Entscheidung, Nutzung, Revocation.

## Auto-Freigabe vs Human Approval

- **Auto-Freigabe** nur fuer niedriges Risiko + passende Policy.
- **Human Approval** bei `restricted`, `secret`, `local_only` oder Remote-LLM-Zielen.

## Delegation

- Delegation erfordert explizit `share` und erzeugt immer einen neuen Grant mit eigener ID und Audit-Referenz.
- `share` ist getrennt von `read_metadata`, `download_encrypted` und `decrypt`.
- Delegierte Grants duerfen nur gleich eng oder enger sein (kleineres Scope, kuerzere Laufzeit, weniger Berechtigungen).
- Delegation an User ist getrennt von Delegation an Worker.
- Delegation an Remote-LLM ist immer explizit und separat (`provide_to_remote_llm`), niemals implizit aus `provide_to_worker`.
- Delegation muss den Ursprung (`parent_grant_id`) behalten, damit ein Widerruf vererbte Grants nachvollziehbar sperren kann.

## Revocation-Wirkung

- Revocation stoppt neue Tickets, Key-Unwraps und neue Context-Releases.
- Bereits lokal entschluesselte Kopien sind nicht vollstaendig rueckholbar (explizit dokumentiert).
