# Artifact Release UI Review

## Ziel

Die UI muss sensible Artefaktfreigaben transparent darstellen, damit Reviews nachvollziehbar und sicherheitskonform erfolgen.

## Pflichtanzeige pro Freigabe

- Artefakt + Version + Datenklasse
- Ziel (User, Worker, Remote-LLM) und Runtime-Kontext
- Transportmodus (`hub_storage`, `webrtc_p2p`, `manual_export`, `disabled`)
- Risikoindikator (lokal vs cloud/remote)
- Ablaufzeit und aktueller Revocation-Status

## Rechte klar getrennt darstellen

Die UI trennt sichtbar:

- `download_encrypted`
- `decrypt`
- `share`
- `provide_to_worker`
- `provide_to_remote_llm`

Keine Sammelcheckbox darf implizit Remote-LLM-Freigaben miterteilen.

## Review- und Gate-Verhalten

- Sensible Kombinationen (z. B. `secret` + `provide_to_remote_llm`) erfordern explizites Review.
- Timeout oder Ablehnung blockiert Freigabe.
- Genehmigungen sind task-/zeitgebunden darstellbar.

## Audit-Sicht

- UI zeigt Audit-Historie fuer sensible Artefakte (Request, Decision, Revocation, Nutzung).
- Events sind mit `grant_id` und `task_id`/`run_id` korrelierbar.
