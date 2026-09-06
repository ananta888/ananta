# Scoped persona profile editing

The optional Hub persona-image composition also enables profile metadata editing.
No worker executes profile management. The existing organization shell exposes a
**Persona & Medien** section; the same operations are fully headless over the API.
The image worker, source evidence and policy configuration remain explicit
prerequisites; enabling an editor does not issue grants or admit source evidence.

## Owners and access

All routes require user authentication and an exact tenant/project context:

`/api/persona-media/v1/projects/{project}/organizations/{organization}/profiles/{kind}/{owner}`

- `organization`: owner is the real organization ID.
- `team`: owner is a real team ID linked to exactly one non-archived organization
  in that project. Shared or ambiguous team ownership fails closed.
- `agent`: owner is the logical `OrganizationRoleAssignmentDB.id`, **not** an
  `agent_url`, physical worker, persona ID or execution-task assignment ID.

Reads require project READ and current organization membership. Writes require
project MANAGE, organization-admin membership and an unexpired, non-revoked
`persona_media`, `organization_admin` or `*` grant for that organization. Worker
and service credentials cannot substitute for user authority. Archived owners
and ended assignments are unavailable; completed organizations are read-only.

The SQL owner adapter, authorization service, immutable profile repository and
image-selection adapter have separate responsibilities (SRP/DIP). The editor
does not extend the existing large topology state service with persona business
logic. Publication remains an independent authorization decision.

## API

GET returns `profile`, `revision`, `content_hash`, `media_available`, `tenant_id`.
An absent profile has revision 0 and null profile/hash. If a stored image is no
longer previewable, the body exposes the current CAS revision/hash but redacts
the whole profile (`profile: null`, `media_available: false`). An authorized
owner can replace or disable it without a manual database repair. A corrupt
profile/head is an error, never a revision-zero default.

PUT accepts exactly `{"profile": <ananta.persona-media.v1>, "expected_revision": N}`.
The profile scope must match the route and authenticated tenant; the new revision
must be N+1. Immutable history and actor/hash audit are written in the same SQL
transaction as the head CAS. Concurrent/stale updates return 409; no automatic
retry or overwrite occurs. Existing repository callers remain compatible and
record a null actor when none was supplied; the user API always supplies it.

An explicit image selection requires the exact active normalized image reference
and current preview permission. `requested_usage` is only metadata, not a grant.
Non-image asset selections are rejected until their admission adapters exist;
missing, inherit and disabled remain valid for all four media kinds.

GET `/api/persona-media/v1/projects/{project}/images/{image}/reference` returns a
closed `reference` after project, active-catalog and image-policy checks. Preview
continues through the authenticated `/preview` PNG endpoint. It does not accept
external URLs or local paths. Responses are no-store and nosniff.

## Editor boundary and remaining integration

The owner selector uses the selected organization and loaded topology team and
assignment nodes. Image selection accepts an already admitted image ID; it does
not enumerate unrelated artifacts or silently create origin/license/consent
evidence. Private Blob URLs are revoked on selection/context changes and teardown;
old requests are cancelled and stale results cannot enter a different scope.

GET the profile route with `/effective` appended returns the computed inherited
media, origin profiles/revisions, topology revision and current preview
availability. The Hub derives ancestry through the exact role assignment,
role slot, organization unit and team link. Archived, ambiguous and cross-scope
parents fail closed. Parent revisions and ancestry are rechecked before output;
a concurrent change returns a bounded error instead of a mixed profile.

The existing pure resolver is shared with complete membership projections; no
invented team/agent/assignment IDs are used for organization-only previews. A
revoked selected asset is redacted and unavailable, not replaced by an implicit
fallback. Disabled stops inheritance. `publication_checked: false` and
`runtime_bound: false` explicitly distinguish this read model from room
publication authority and an actual dispatched execution.

The response also includes a closed `selection` configuration pin. A caller may
pass it to the separately authorized [profile-bound bounded Meet turn](meet-persona-profile-turns.md)
path. The pin grants no access and is not an evidence identity. Active runtime
lifecycle and image/publication policies are checked again at dispatch and lease
checkpoints; a metadata preview alone cannot authorize execution.

The editor displays the selected revision, explicit fallback state and persisted
effective profile with origin, classification and preview availability. Unsaved
form changes do not pretend to be the stored effective profile. An inherited
image may be previewed without turning it into a local explicit override.
Saving does not join a room, open a camera/microphone, share a screen, publish an
asset or switch an existing renderer. Voice/video/style admission, effective
asset listing and runtime generation-bound switching remain separate work.

Tests use synthetic authorization/asset inputs and do not constitute grounded
production release evidence. No public Hub or Meet deployment is enabled by this
source change.
