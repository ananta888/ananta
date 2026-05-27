# KRITIS Audit Coverage Target Model

Dieses Zielmodell definiert, welche Operationen in Ananta zwingend auditierbar sein muessen und welche Payload-Klassen erlaubt sind.

## 1) Pflicht-Auditbereiche (mandatory)

Folgende Operationen muessen immer ein Audit-Event erzeugen:

1. LLM-Generierung (Planung, Ausfuehrung, Repair, Zusammenfassung)
2. Tool-Aufrufe (inklusive Start/Ende, Outcome, Zielscope)
3. Approvals/Rejections/Expiry
4. Mutationen (Datei, Code, Artefakt, Task-State, Systemaktion)
5. Workflow-Uebergaenge (kritische Statuswechsel)
6. Repair-Aktionen
7. High-risk Reads (secret/local-only/restricted context classes)

## 2) Event-Mindestfelder

Jedes mandatory Audit-Event muss enthalten:

- `trace_id`
- `task_id` (falls vorhanden)
- `actor`
- `role`
- `policy_version`
- `operation_type`
- `target`
- `outcome`
- `timestamp`

## 3) Payload-Klassen

### Mandatory erlaubt

- strukturelle Metadaten (IDs, Rollen, Policy-Version)
- Ergebnisstatus (allow/deny/failed/success/retry/rollback)
- Kontextklassifikation (repo/artifact/task_memory/wiki/external/user)
- Zielklassifikation (path class, artifact id, operation class)

### Optional erlaubt

- redigierte Vorschaufragmente
- Performance-/Latenzmetrik
- Modell-/Provider-Metadaten

### Verboten

- rohe Secrets/Credentials
- unredigierte sensitive Nutzdaten
- vollstaendige private Inhalte ohne explizite Freigabeklasse

## 4) Kettenfaehigkeit

Audit-Events muessen korrelierbar sein ueber:

- `trace_id` (session-/request-weit)
- `task_id` (task-weit)
- optionale `parent_event_id`/`step_id` bei mehrstufigen Flows

## 5) Fail-closed Erwartung

Wenn mandatory Audit fuer einen mandatory Flow nicht erzeugt werden kann, soll der betroffene mutation-capable Schritt blockiert werden.
