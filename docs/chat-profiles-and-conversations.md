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

The configuration and process-binding APIs are additive as well:

- `GET /api/chat/settings/schema` returns the deterministic setting catalog used by Angular.
- `GET /api/chat/profiles/<profile_id>/effective` exposes delta, effective value, and provenance.
- `GET /api/chat/sessions/<session_id>/process` resolves a session override before its profile binding.
- `POST /api/chat/sessions/<session_id>/process/clone` creates a session-owned graph before editing.

Profile writes are strictly validated. A `null` value in a profile PATCH resets
that single setting to inheritance. Unknown keys and plaintext credential keys
are rejected; provider credentials are referenced through
`chat_backend_credential_ref`. Existing unknown values remain preserved by the
Angular editor when an old profile is duplicated or edited.

## Visual-process binding and live state

Profiles and conversations may carry a nullable `process_ref` containing a
`graph_id` and version. A conversation reference overrides the profile
reference. The persisted graph remains an immutable process definition from
the runtime viewer's perspective: workflow status is represented separately as
a runtime overlay keyed by step ID. Polling must never copy `run_state`, model
selection, attempts, or call profiles into the graph definition.

The AI-Snake `Prozess` tab embeds the same visual-process editor used by the
standalone route. A profile-owned graph must be cloned before session-specific
changes. Run and gate actions continue to use hub endpoints; workers do not
coordinate or signal each other.

## Using the Angular configuration

Open **Sessions → Profile** and duplicate a built-in profile or create a new
one. Name, icon, description and system prompt are edited under the profile;
all runtime fields are generated from the server schema. Choose backend,
model, API base and an `env://VARIABLE_NAME` credential reference. **Modelle
laden** refreshes the draft-specific model list, while **Verbindung testen**
distinguishes unsupported provider, missing credential, authentication,
timeout, unreachable endpoint and unknown model. A failed test is a warning
and does not destroy or automatically save the draft.

An outlined reset arrow removes only that scope's delta. Session settings use
the identical controls and override the profile. The global panel is explicitly
labelled as defaults and cannot remove either profile or session deltas.

The **Prozess** section lists stored definitions and versions. Sessions can
explicitly inherit the profile process, select another definition, create a new
one or clone the inherited graph before editing. The chat-level Prozess tab is
read-only, shows live/historical runs, and keeps the immutable run snapshot.

## Adding a setting

Add its definition to the canonical catalog projection in
`agent/services/chat_setting_catalog.py`, including scopes, type, constraints,
visibility, secret and advanced metadata. Add or align the runtime default and
an API test that asserts schema and merge behavior. Angular requires no static
field entry: every allowed scope consumes `ChatSettingControlsComponent`.
Regenerate [the inventory](chat-setting-inventory.md) afterwards.

## Credential and export security

Profiles contain references, never tokens. The supported local resolver form
is `env://VARIABLE_NAME`; resolution happens only inside the hub probe. Browser
responses contain model IDs and safe error codes, never authorization headers
or resolved environment values. Runtime overlays remove keys containing
`secret` or `credential`, and documentation/test exports must use synthetic
references only. There is deliberately no browser-side secret editor.

Known remaining non-goals are a general-purpose credential vault and automatic
external discovery without a user action. Provider probing is bounded to five
seconds and manual model IDs remain supported.

Legacy clients can continue reading effective `system_prompt` and `settings`
directly from a session response.
