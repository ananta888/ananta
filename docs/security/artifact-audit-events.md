# Artifact Audit Events

## Ziel

Ein konsistentes Event-Schema fuer Grant-, Key-, Transfer- und Context-Release-Vorgaenge.

## Eventgruppen

1. **Grant Lifecycle**
   - `grant_requested`
   - `grant_approved`
   - `grant_denied`
   - `grant_revoked`
2. **Key/Decrypt**
   - `key_unwrapped`
   - `decrypt_allowed`
   - `decrypt_denied`
3. **Transfer**
   - `transfer_started`
   - `transfer_completed`
   - `transfer_failed`
4. **Context Release**
   - `context_released_to_worker`
   - `context_released_to_remote_llm`
   - `context_release_denied`

## Mindestfelder pro Event

- `event_id`, `event_type`, `timestamp`
- `actor_type`, `actor_id`
- `artifact_id`, `version_id`
- `task_id`/`run_id` (falls vorhanden)
- `grant_id` (falls vorhanden)
- `decision` und `decision_reason`

## Korrelation

- Korrelation ueber `request_id`/`trace_id`, damit Request -> Entscheidung -> Nutzung nachvollziehbar ist.
- Revocation-Events referenzieren `parent_grant_id`, wenn Delegationen betroffen sind.
