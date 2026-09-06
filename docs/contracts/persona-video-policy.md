# Explicit registered-source video permissions

`PersonaVideoPolicy` adds mandatory `media_kind: video` to the shared immutable
permission terms. A prior image-policy body cannot parse as a video policy and
the image model still rejects the additional video field. Image serialization,
source pins, diagnostic codes and SQL table names remain compatible.

The video service uses `PersonaVideoPolicyDomain` and its own
`persona_video_policy_heads` / `persona_video_policy_versions` tables.
There is no lookup fallback to image rows. Service and repository both reject
the other policy class. Video sources must be immutable Hub-registered
`persona_video` origins, with matching tenant/project and binding digest.
A policy cannot relabel a registered image origin as a video source.

Source, license and consent identifiers are separately registered pins. License
and consent proofs must have their dedicated origin kinds. Explicit project
management authority installs/revokes policy; worker/service credentials cannot
do so. Eligible users, current project access, requested purpose, expiration,
all proof bindings and exact policy revision are checked for every admission
and subsequent use. The policy's explicit video grant is still needed even
when a source/license document was already admitted for another purpose.

Consent is mandatory for personal likeness and all uploads; generated clips
cannot be classified production. Synthetic or test-scope proofs may satisfy
only `test_only` policies. No policy grants voice/face cloning, arbitrary
execution or implicit publication. Preview-only permission does not publish.
Human interaction is not required: an authorized Hub policy/API flow can
provide these terms automatically, without bypassing any mandatory condition.

The existing revision-CAS repository is shared through a narrow composition,
not copied: each change is an immutable audited version, revocation consumes
a tombstone revision, stale regrants fail, and concurrent installations admit
only one winner. Payload hashes and exact scope/source/head matching reject
mutated storage. Tables remain separate for the two media kinds.

SOLID: policy terms reuse immutable validation, domain adapters isolate media
kind/admission extraction, the service owns authority checks, and SQL owns
revision persistence (SRP/OCP/DIP). The image domain's prior source-kind
compatibility is intentionally preserved; only the new video domain imposes
the `persona_video` origin requirement. No productive image policy is migrated.

Tests use actual Hub Evidence Registry test identities and separate temporary
policy tables, automated access fixtures and bounded concurrent operations.
No actual usage policy, source grant or production service is installed here.
The concrete video inspection task/receipt transport, admission/revocation API
and profile/publisher binding are still required before productive clip use.
The combined video/image policy, inspection, storage, catalog and retention
run passed 104 tests in 73.52 seconds.
