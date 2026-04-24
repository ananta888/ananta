# Instruction Layer Golden Path

This is the canonical flow for combining a persistent profile with a scoped overlay.

## 1. Create a persistent profile

`POST /instruction-profiles`

- set `name`
- set `prompt_content`
- optionally set `is_default=true`
- optionally set `profile_metadata.preferences`

## 2. Create an overlay for a target scope

`POST /instruction-overlays`

- set `name`
- set `prompt_content`
- set `attachment_kind` (`task`, `goal`, `session`, `usage`)
- set `attachment_id` when required

## 3. Bind profile + overlay to runtime context

Use one of:

- `POST /tasks/{task_id}/instruction-selection`
- `POST /goals/{goal_id}/instruction-selection`

Payload fields:

- `owner_username`
- `profile_id`
- `overlay_id`

## 4. Inspect effective stack

`GET /instruction-layers/effective?task_id=...`  
or  
`GET /instruction-layers/effective?goal_id=...`

Validate:

- selected profile and overlay
- effective merged preferences
- applied/suppressed layers diagnostics

## 5. Execute task

Task proposal/execution uses the assembled instruction stack and surfaces diagnostics in worker context metadata.
