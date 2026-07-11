# Chat profiles and conversations

Ananta separates reusable AI configuration from user conversations.

## Model

- A **chat profile** owns the system prompt and default runtime settings such
  as backend, CodeCompass use, retrieval profile, answer limits, and memory
  policy.
- A **conversation** owns its name, folder assignment, message history, active
  state, and a `profile_id` reference.
- Conversation-level `settings_delta` and `system_prompt_override` values are
  optional. They take precedence over the selected profile without modifying
  that reusable profile.
- Folder hierarchy organizes conversations only. It does not change their
  runtime configuration.

Built-in profiles are read-only. User profiles are persisted under
`chat_profiles` and can be created, edited, or deleted while unused.

## Effective configuration

The runtime materializes configuration in this order:

1. global chat defaults;
2. selected profile settings and system prompt;
3. conversation-specific settings and prompt overrides.

The materialized values remain present in the conversation record for
backward compatibility with existing TUI and AI-Snake consumers.

## Migration

Migration to `chat_model_version = 2` is automatic and idempotent.

- Existing conversation IDs, names, folders, active selection, and message
  channel IDs remain unchanged.
- A legacy built-in session such as `code-help` receives the matching
  `profile_id`. Existing user overrides are reduced to values that actually
  differ from that profile.
- Custom legacy conversations receive the `general` profile unless they
  already reference another profile.
- New installations start with one writable `Neuer Chat` conversation using
  the `general` profile, plus the backend-owned `Visual Snake Log`.

The profile and conversation APIs are additive:

- `/api/chat/profiles`
- `/api/chat/profiles/<profile_id>`
- `/api/chat/sessions`
- `/api/chat/folders`

Legacy clients can continue reading effective `system_prompt` and `settings`
directly from a session response.
