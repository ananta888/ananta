# Artifact Access Grants

## Datenmodell: `ArtifactAccessGrant`

Minimalfelder:

- `id`
- `artifact_id`
- `version_id`
- `subject_type` (`user|team|role|worker|device|task`)
- `subject_id`
- `permissions` (Liste)
- `expires_at` (optional)
- `grant_reason`
- `status` (`active|revoked|expired`)
- `created_by`, `created_at`, `updated_at`

## Berechtigungen

- `read_metadata`
- `download_encrypted`
- `decrypt`
- `share`
- `provide_to_worker`
- `provide_to_remote_llm`

## Regeln

1. Grant gilt standardmaessig versionsgebunden (`version_id` Pflicht fuer sensitive Klassen).
2. `download_encrypted` ist getrennt von `decrypt`.
3. `share` ist getrennt von `read_metadata`/`decrypt`.
4. `provide_to_remote_llm` ist separat, niemals implizit aus `provide_to_worker`.

## Subjektmodell

- User/Team/Role fuer organisatorische Freigaben.
- Device fuer geratespezifische Decrypt-Rechte.
- Worker/Task fuer laufzeitgebundene Releases.

