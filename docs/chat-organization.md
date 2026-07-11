# Chat organization

Ananta keeps six chat concepts separate:

- A **profile** defines how the AI works: system prompt, backend, tools, RAG, and defaults.
- A **type/subtype** classifies what a conversation is about.
- A **conversation** (the backward-compatible API name remains `ChatSession`) owns its identity, message history, profile and classification references, optional overrides, and folder placement.
- A **folder** is user-controlled navigation. It does not define AI behavior or semantic type.
- A **reorganization proposal** is a persisted, editable operation list. Creating or validating it never mutates the active structure.
- An **organization revision** is an append-only record of one atomically applied user or AI mutation.

The public session endpoints retain `session_id` for compatibility. New organization operations use `target_id`; a `conversation.*` target is the existing session ID. No second conversation store is introduced.

## Data flow

```text
current folders + conversation metadata
                  |
                  v
        persisted proposal (draft)
                  |
          simulate + validate
                  |
           ready / invalid
                  |
        explicit operator apply
                  |
   one locked compare-and-swap write
     /            |             \
structure   proposal=applied   revision
                                  |
                         conflict-checked revert
                                  |
                         new append-only revision
```

`base_state_hash` is calculated from a canonical snapshot ordered by stable IDs. Apply rejects a proposal when the current hash differs. A successful write persists folders, conversations, proposal status, and revision together through the atomic `UserConfigManager` replacement write. Repeating Apply returns the original revision without executing operations again.

Revert is allowed only when the selected revision's result hash is the current organization hash. It never deletes history: it restores the stored structural snapshot and appends another revision. Later or external changes produce `revision_conflict` instead of being overwritten.

## Privacy

AI reorganization defaults to `metadata_only`. It sends identity, name, type/subtype, profile ID, current folder path, and message count, but no messages or preview text. `metadata_plus_preview` is an explicit operator choice and caps each preview. Full conversation history and system prompts are not organization inputs.

## Persistence and migration

Existing `chat_sessions`, `chat_folders`, `chat_profiles`, and `chat_session_types` remain valid. Missing `sort_order`, proposal, and revision collections default to empty/zero values, so existing IDs are preserved. Legacy `group` remains available as a weak fallback signal and is not treated as a folder or type.

The legacy `POST /api/chat/sessions/ai-reorganize` endpoint remains non-mutating. It now creates and validates the same persisted proposal used by `/api/chat/organization/proposals`, while still returning `folders` and `assignments` for transitional clients. New clients edit, validate, and atomically apply the returned proposal ID.

## Responsibility boundaries

`agent.routes.chat` is the HTTP adapter. `ChatOrganizationService` owns snapshots, validation, application, and revisions through a narrow persistence port. `UserConfigManager` owns JSON-safe, atomic file replacement. Angular's `ChatSessionsService` is the API facade; the UI never treats a local proposal or history copy as canonical.
