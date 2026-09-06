# Profile-bound bounded Meet turns

This additive v1 path selects a static persona image for a **new bounded media
turn**. It does not implement long-lived MDS session switching or a talking head.
Both persona-image and Meet-media Hub composition must be explicitly enabled.
Public Hub/Meet configuration is not changed by installing these source files.

## Selection and dispatch

Read the authorized profile `/effective` endpoint documented in
[persona-profile-api.md](persona-profile-api.md). Its `selection` object contains
`organization_id`, `owner_kind`, `owner_id`, and `selection_digest`.

Pass that exact object as `persona_profile` in the existing bounded Meet turn
request, alongside `text` and optionally `publish_to_meet`. `persona_image_id`
and `persona_profile` are mutually exclusive. Existing requests with neither
retain the neutral avatar behavior.

The selection digest binds tenant/project, selected owner, real ancestry,
topology/lifecycle change token and all participating immutable profile hashes.
It is a configuration hash, **not** an SRC/RUN identity, execution assignment,
grant or bearer credential. Do not invent it or treat a matching hash as access.

The Hub rechecks project and organization membership, derives current ancestry,
requires active organization/team/unit/role-slot/assignment lifecycle where
applicable, compares the pinned selection and obtains the exact admitted image.
Image preview/publish authorization and the separate machine room grant remain
independent checks. A draft, paused, retired or suspended runtime context cannot
be promoted into an active media run merely because its profile can be viewed.

The profile pin stays in the existing Hub task's `meet_media.persona_profile`
context. Only the closed `persona_image` bytes/reference and existing task/lease
envelope are sent to the worker. No new worker-side orchestration or independent
task queue is introduced. The running image worker contract is unchanged.

## Revocation and limits

After generation, and on the existing image/publication lease callbacks, the Hub
rechecks the selected profile, membership, active lifecycle and image policy.
A changed inherited profile or topology pin, revoked membership/image, paused
organization or ended assignment rejects the result/lease. The existing renderer
and publisher stop at their bounded authority checkpoints; this is not a claim
of instantaneous deletion of bytes already transmitted.

Missing or disabled profile images are blocked for this explicit selection path.
There is no silent neutral fallback. An independent ordinary turn without profile
selection remains a separate existing request under its own policy.

Saving a profile does not hot-swap an active turn. A subsequent bounded turn must
use a freshly read selection. Generation-fenced long-lived session switching,
pending media queue invalidation across the new MDS transport and an explicit
neutral fallback policy remain MAP-20 work. Profile pins do not become grounded
production release evidence: Registry issuance and verified runs remain separate.

The organization read adapter, profile policy service, Meet selection adapter,
Hub task lifecycle and worker renderer retain separate responsibilities (SRP/DIP).
No source/persona contents are written into task audit events.
