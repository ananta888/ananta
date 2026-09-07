# Video references in persona profiles

The existing closed `ananta.persona-media.v1` model already has a video
selection. A configured `PersonaProfileVideos` port now validates that
selection against current project/preview permission, active private video
catalog revision and the registered immutable inspection receipt. Exact
tenant, project, kind, artifact version and hash are required. The reference
lookup rechecks catalog state after policy verification; metadata is not a
permission grant and contains no MP4 bytes or storage paths.

`GET /api/persona-media/v1/projects/{project}/videos/{artifact_id}/reference`
uses normal user authentication and private response headers. The returned
reference can be saved through the existing organization/team/agent profile
API. Its model/schema/content-hash format is unchanged. Image-only deployments
still reject video assets when no video port is installed; video-only
deployments do not fabricate an image port. Voice/style asset selections
remain unsupported instead of silently choosing a provider or generator.

Existing lineage rules apply independently to video: agent, then team, then
organization; an explicit disabled value stops inheritance. A revoked or
unavailable selected video stays unavailable rather than falling back to a
different persona. Current profile retrieval retains the CAS revision while
hiding unavailable references, allowing an authorized owner to replace them.
Effective selection remains a preview and explicitly reports that publication
has not been checked and no runtime binding has been granted.

The additive `for_video_execution` service path requires current organization
runtime/owner state, the exact profile selection digest, an available selected
video, and non-disabled voice/video outputs. It returns only the immutable
reference. Missing voice can use a separately authorized fixed speech engine;
an explicit unsupported voice asset is never replaced. Actual publication
still needs separate video usage policy and current worker/task authority.
Legacy image execution retains its declared image/voice/video outputs and
rejects an explicit video asset instead of ignoring it.

SRP/OCP/ISP: image and video expose the same narrow reference-checking port.
Shared profile composition has moved out of the image-specific bootstrap, so
either media domain can supply independently validated references. This cleans
up the prior mixed image/profile initialization responsibility. The existing
profile service still coordinates authorization, lineage and response metadata;
that broader pre-existing responsibility is preserved, not a new worker loop.

Tests combine real organization topology/profile CAS, private video policy,
Hub tasks and Registry receipts with an explicitly structural decoder double.
They cover all three owner kinds, inheritance/disable, stale or revoked
references, editability after revocation, catalog races, exact runtime pins,
disabled outputs and authenticated metadata-only API use. Clip publication and
the Angular video picker remain subsequent integration work. No live meeting
or production runtime is enabled by saving a profile.

The combined video/image profile, output-control, bootstrap and existing Meet
profile regression passed 80 tests in 60.94 seconds. One initially inactive
organization in the runtime test fixture was corrected before that passing run.
