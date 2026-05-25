# Artifact Object Policy

## Ziel

Berechtigungsentscheidungen fuer Artefakte muessen deterministisch am Objektzustand haengen, nicht an LLM-Ermessen.

## Input fuer die Policy

- Artifact-Metadaten (`artifact_id`, `version_id`, `owner_id`, `task_id`)
- Klassifikation (`public`, `internal`, `restricted`, `secret`, `local_only`)
- Subjekt (`user`, `team`, `role`, `worker`, `device`)
- Zielkontext (lokaler Worker, Cloud-Worker, Remote-LLM)
- Angeforderte Aktion

## Aktionsmatrix (Auszug)

1. `download_encrypted`
   - Erlaubt Ciphertext-Download nach Grant/Policy.
   - Erlaubnis bedeutet **nicht** Decrypt-Recht.
2. `decrypt`
   - Erlaubt CEK-Unwrap/Decrypt nur bei passendem Decrypt-Grant.
   - Fuer `local_only` nur auf lokalen Runtimes.
3. `provide_to_worker`
   - Kontextfreigabe an Worker nach Release-Gate.
4. `provide_to_remote_llm`
   - Immer eigenes, explizites Recht.
   - Nie implizit aus `provide_to_worker`.

## Deterministische Regeln

- Default-Deny.
- Policy-Auswertung nutzt nur explizite Felder aus Grant, Artefaktzustand und Zielruntime.
- Keine probabilistische oder LLM-basierte "Best-Effort"-Entscheidung.
- Gleicher Input muss immer zur gleichen Entscheidung fuehren.

## Beispielregeln

- `restricted` + `provide_to_remote_llm` -> deny ohne expliziten Grant und Approval.
- `local_only` + Ziel=Cloud-Worker -> deny.
- `download_encrypted` erlaubt, `decrypt` fehlt -> Download ja, Entschluesselung nein.
