# Persona presentation metadata v1

`agent.models.persona_media` defines immutable, closed metadata contracts.
`agent.services.persona_media_resolution` contains their pure resolution logic.
Neither component grants access, issues an identity, loads files or starts work.
This separation keeps presentation, authorization and execution independent.

Each profile belongs to exactly one tenant, project and organization/team/agent
owner. A separate `persona_id` and positive profile revision identify its
presentation. These fields are not worker identity, a role slot, a role assignment
or a security principal. The canonical profile hash supports immutable storage
and session binding; changing content under a bound revision must be rejected by
the repository/application layer, not silently accepted as a profile update.

`SqlPersonaProfiles` stores immutable revision payloads with canonical hashes.
An atomic head compare-and-swap permits only the next revision; concurrent or
stale writes conflict. Reads require the exact scope, revision and expected
hash, and revalidate stored content. No lookup falls back to another owner or
the latest version. Initialization is explicit; importing the contracts does
not create database state. The repository itself is not an authorization API.

Image, voice, video and style slots each have exactly one state:

| State | Resolution |
| --- | --- |
| `missing` | No local selection; seek a parent and preserve the missing-state trace. |
| `inherit` | Explicitly use a parent; preserve the explicit inheritance trace. |
| `disabled` | Stop here; never fall back to a parent's asset. |
| `asset` | Use the exact local artifact revision/digest, subject to separate authorization. |

Resolution has fixed precedence: agent, team, organization. Input order cannot
change it. Duplicate owner layers and mismatching tenant/project/owner bindings
fail closed. The caller must supply a **trusted Hub membership projection** with
its assignment and membership revision, not membership copied from user input.
The result preserves that binding and the inspected profile revisions/states.
Missing profiles never produce a generated or implicit default image/voice.

Asset references contain only tenant/project, artifact ID, positive revision,
SHA-256, media kind and explicit production/synthetic/test-only classification.
Cross-project references are rejected even within one tenant. There are no URLs,
file paths, binary payloads, model secrets or provider credentials. Usage classes
are requests, not permissions. Resolving `requested_usage: ["publish"]` cannot
promote a test-only asset or create publishing/voice-cloning consent.

Integration still required in the next tasks: authorized immutable persistence,
artifact admission and revocation (MAP-18), the editing UI (MAP-19), and current
membership/profile/asset authorization rechecks around session transitions
(MAP-20). Do not expose this pure resolver directly as a caller-authorized API.
Tests in `tests/test_persona_media.py` use only deterministic synthetic metadata.
