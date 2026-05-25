# Worker Artifact Key Access

## Ziel

Worker erhalten Entschluesselungszugriff nur fuer explizit freigegebene Artefakte im erlaubten Laufzeitkontext.

## Zugriffsmodell

- Kein globaler Worker-Masterzugriff.
- Jede Freigabe ist an ArtifactVersion + Aktion + Zielworker gebunden.
- Zugriffstickets sind task-, run- und/oder zeitgebunden.

## Laufzeitgrenzen

- Lokale Worker koennen strengere Klassen (`local_only`) verarbeiten.
- Cloud-Worker duerfen nur Klassen verarbeiten, die Policy explizit freigibt.
- Remote-LLM-Kontext bleibt ein separates Recht (`provide_to_remote_llm`).

## Ticket-Lifecycle

1. Worker fordert Kontext fuer Task/Run an.
2. Hub prueft Grant + Policy + Runtime.
3. Bei Erfolg: kurzlebiges Zugriffsticket/Key-Material-Referenz.
4. Nach Ablauf: neues Ticket nur nach erneuter Pruefung.

## Delegationsgrenzen

- Worker duerfen keine neuen Grants fuer andere Worker erzeugen.
- Worker duerfen keine Rechte erweitern (kein Scope-Upgrade, keine Laufzeitverlaengerung).
- Jede Nutzung wird mit `task_id`, `run_id` und `worker_id` auditiert.
