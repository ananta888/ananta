# Hub-delegated video inspection and Registry receipts

The existing inspection lifecycle now composes an explicit image or video
format adapter. Video uses `ananta.persona-video-task.v1`, ordinary
`persona_video_inspection` Hub TaskQueue records, and a closed
`persona_video` execution context. It never creates a worker-owned queue.

Before execution the Hub checks video-specific policy authority, validates
source bytes/MIME and reserves an actual Registry run bound to tenant/project,
normal task, assignment, dispatch lease, repository revision, source inputs,
execution-profile digest and environment digest. The worker receives only the
closed assignment projection and input bytes. The fixed deadline is twenty
seconds; repeated or post-hoc identity creation is not a recovery mechanism.

Video deliberately requires a policy port that affirms video capability.
A legacy image-only port is not accepted for video. Existing image adapters
without the new optional kind-check method retain their image-only behavior.
The concrete policy service checks both kinds explicitly.

`HubPersonaInspectionLeases(kind="video")` rechecks the actual task state,
kind, complete assignment context, initiating user's current policy, admission
digest and exact Registry projection. Background checks do not inherit an
administrator override. Image/video assignment validators share syntax checks
but keep distinct required schema values. A video assignment cannot be
completed through an image task-state adapter.

Result receipts bind source/output hashes, actual normalized frame count,
video/preview sizes and the fixed normalization profile. They contain no media
bytes. The Hub accepts completion only under the original task/lease CAS and
records the matching Registry result. Revocation, worker failure and cancelled
tasks produce no successful result. Changed frame metadata also changes the
receipt; it cannot be attached to an earlier successful inspection run.

Later asset verification rechecks the exact registered result and evidence
classification. Registry-issued test-scope identities remain test-only even
when all task/receipt checks pass. Permission and active catalog checks remain
separate from this receipt verification.

SRP/OCP/DIP: task orchestration, task persistence, current-authority checks and
small format/receipt adapters are separate. Existing image helper imports,
wire fields and receipt digests remain compatible. Shared helpers have no
worker decoder dependency; actual decoding stays on the worker. The immutable
video metadata receipt can be verified after normal task archival.

Tests exercise actual Hub TaskQueue/CAS, Registry source/run issuance and the
video policy repository with a clearly classified structural decoder double.
They check pre-reservation, terminal state, media-kind isolation, scope/owner/
lease/deadline/source mutation, result-frame mutation and policy revocation.
This is not a claim of actual decoder execution in those task tests; separate
CPU/GPU gates cover the local codec paths.
The combined Hub-task, image/video policy and decoder-contract run passed
114 tests in 78.98 seconds. No public feature or Registry release was activated.

The private authenticated worker HTTP transport/live-lease route is documented
in [persona-video-http.md](persona-video-http.md). Still required to expose
productive upload: lifecycle/API/bootstrap composition and profile/publisher
integration. No public feature or trust policy is enabled.
