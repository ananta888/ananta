# Private immutable video bundle storage

The video catalog uses a new closed `ananta.persona-video-asset.v1` model:
one `video` reference, one `image` preview reference, the immutable admission
snapshot and a complete inspection task/assignment/lease/Registry-run binding.
References must be distinct v1 artifacts with identical tenant, project and
classification. The model pins normalized clip profile, frame count and byte
sizes. Source identifiers must have the registered-source syntax; actual
Registry/policy/result validation remains a required service-layer step.
Model validity or merely reserving a test run never grants publication rights.

`PersonaVideoStorage` composes the existing immutable ArtifactStore. It writes
exactly `clip.mp4` and `preview.png` under separate artifact identifiers, at
version one, with all source/output hashes and lengths checked beforehand.
Authority is rechecked around each write/read. No caller supplies a pathname,
public URL or MIME override. A partial write must remain covered by the
caller's durable pending/revoked catalog entry and storage fence.

`create_video_asset_catalog` creates separate `persona_video_assets` and
`persona_video_asset_events` tables. It uses the existing SQL CAS/audit/
write-fencing mechanism through `SqlPersonaAssetCatalog`, composed with a
small video format adapter. The compatible `SqlPersonaAssets` facade retains
the old image table/column names, payload model, file names and method calls.
There is no image-data rewrite or automatic image-to-video migration.

This separates SQL transaction responsibilities from format details (SRP,
OCP, DIP) without duplicating the race-sensitive lifecycle. The existing
two-part catalog design is deliberately preserved; it is not a general
unbounded multipart artifact abstraction. No dynamic format is user-selected.

Pending → active/failed/revoked, active → revoked, retired → purging → purged
transitions consume exact revisions. Activation requires both private
Artifact/ArtifactVersion records. Missing members roll back the state and
audit transition. The SQL storage fence excludes concurrent revocation;
stale writers cannot reacquire it. Pure catalog transitions do not themselves
verify policy or perform physical erasure.

`persona_media_video`, like persona images/previews, is hidden from generic
artifact APIs, browsers and context surfaces. The dedicated image catalog
cannot parse or resolve a video asset. Private video access still requires a
future dedicated policy-checked Hub service; no video route is enabled here.

Tests use actual Registry-issued test identities and temporary stores/databases.
The tiny MP4 header in storage tests is explicitly a structural test double,
not successful media decoding or a completed production run. The actual
decoder/RTX speech-renderer gates are documented separately.
The combined storage/catalog/image-erasure/retention/inspection/artifact run
passed 139 tests in 98.65 seconds; no public or real user files were changed.

Still required before productive upload/use: video-scoped permission policy,
real Hub task/receipt/worker transport integration, orchestration of admission
and revocation, automatic video retention, profile/publisher/UI
integration. Existing image retention must never be applied to MP4 files by
pretending they are image assets.

Exact resumable physical erasure is now provided by the separate composition
described in [persona-video-erasure.md](persona-video-erasure.md); it does not
automatically activate deletion or a retention schedule.
