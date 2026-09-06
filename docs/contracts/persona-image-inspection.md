# Persona image inspection port

This worker-only execution component normalizes **static PNG/JPEG** pixels. It
does not admit an asset, decide its license, infer consent, issue a grant or
publish anything. It is one implementation building block for MAP-18, not the
completed Hub upload/asset lifecycle.

`PersonaImageInspector` requires an assignment-bound authorization checkpoint
and a finite monotonic deadline no more than 30 seconds away. It supervises a
child for at most five seconds with the existing Voice subprocess runner. The
child receives only bytes and a closed MIME value; no caller path, URL, provider
configuration or secrets cross the pipe. Output is capped at 8 MiB. The same
authorization checkpoint is used during execution and before returning results.
A revoked or expired assignment cannot release previously decoded pixels.

The decoder accepts at most 5 MiB and dimensions up to 2048×2048, rejects
mislabelled files and animation, and normalizes orientation. It creates fresh
RGBA pixels, removing EXIF, comments, ICC profiles and other metadata. The
sanitized PNG is at most 1024×1024; its preview is at most 256×256. Both have
separate SHA-256 identities, bound to the original input digest. No image bytes
appear in result representations. Nothing is persisted by these components.

The wrapper rechecks the closed response, input digest, output hashes, dimensions
and normalized PNG headers. The decoder is deliberately kept separate from
subprocess supervision (SRP). Pillow's process-wide pixel limit is **not changed**.
The existing semantic-media compute handler still sets that global limit; it is
left unchanged and is not reused as an asset-admission boundary. Per-image
limits avoid introducing that implicit coupling into the new inspector.

`tests/test_persona_image.py` generates tiny synthetic images in memory. It checks
metadata removal, deterministic output, animation/MIME/dimension rejection, a
real supervised child and authority revocation. These tests require no person,
network, model or live image fixtures.

Still required before public activation: dispatch through a normal Hub artifact
inspection task; origin/license/consent admission from authoritative Hub policy;
separately authorized image selection/publication. Audio,
voice-model and video asset admission are separate profiles, not implicitly
accepted by this image-only port.

## Hub catalog and lifecycle

`PersonaAssetService` requires policy and inspection-task ports. It never runs
the decoder in the Hub. A policy snapshot binds the exact input digest, project,
origin/license/consent references, classification and policy revision. These
references are not trusted merely because a caller supplies strings. A production
policy adapter must validate their authoritative records, and the task adapter
must create/delegate/verify a normal Hub task under its exact dispatch lease.
Neither concrete adapter nor a public activation route is supplied in this slice.

The implemented application service rechecks authority before inspection, before
and during storage, and after activation. It uses `PersonaAssetStorage` around
the existing `ArtifactStore.store_immutable_bytes`/`load_immutable_bytes` methods.
The storage adapter depends only on read-only inspection properties, not on the
worker decoder or Pillow (DIP/ISP). Raw original images are not persisted.

`SqlPersonaAssets` atomically reserves a scoped pending catalog record and both
generic artifact/version records. From their first write, image and preview carry
system-managed kinds that deny generic browsing, downloads and index/context
paths. After verified storage, a revision compare-and-swap activates the bundle.
Failure/revocation is terminal; a delayed activation cannot revive it. Every
state transition has an actor-bound, content-free event in the same transaction.
Active reads revalidate immutable metadata, scope and payload digest.

The service checks lookup access before revealing catalog state, then checks
asset-specific permission and the current catalog revision before and after
loading bytes. Preview and publication are separate policy purposes. A revoked
license/consent cannot release bytes already loaded into memory. No preview
starts a meeting or grants publication. Retention/deletion of already written,
revoked bytes and fencing already-running publishers remain later integration
work; a tombstone denies access but does not physically erase files.

`tests/test_persona_assets.py` exercises real isolated SQL/catalog and immutable
storage with explicit synthetic policy/task fixtures. Combined persona,
ingestion and artifact-route regression: **104 passed in 32.77 seconds**.

## Local worker observation

Image `sha256:c3cd863991e83a6e383c537c8d387b211d2d2d590785e02057efbbd5a26cf394`
includes the image-inspection port and finite ASR deadline validation at
`ba1063d4a`. It passed the three existing local GPU ASR/media/chat tests in
**19.00 seconds**, plus a synthetic in-memory image through the real supervised
child in that container. Neither observation is production release evidence or
proof of live Meet publishing/receive authorization.
