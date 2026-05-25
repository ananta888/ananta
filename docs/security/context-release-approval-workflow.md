# Context Release Approval Workflow

## Ziel

Sensible Kontextfreigaben erhalten einen klaren Human-Approval-Pfad mit nachvollziehbaren Grenzen.

## Anzeige im Approval

Das Review zeigt mindestens:

- Artefakt + Version
- Ziel-Worker und Runtime/Provider
- LLM-Backend (lokal vs remote)
- Datenklasse und Risikoindikator
- angeforderte Rechte und geplante Laufzeit

## Entscheidungsoptionen

- **Approve** mit zeitlicher Begrenzung
- **Approve task-scoped** (nur fuer konkrete `task_id`/`run_id`)
- **Deny**

Remote-LLM-Freigaben sind als eigener, klar sichtbarer Entscheidungspunkt markiert.

## Timeout und Fail-Safe

- Timeout gilt als Ablehnung.
- Ohne explizites Approval bleibt der Kontext blockiert.

## Audit

- Approval-Request, Entscheidung, Begruendung und Ablaufzeit werden protokolliert.
- Folgefreigaben ausserhalb der genehmigten Grenzen werden automatisch abgelehnt.
