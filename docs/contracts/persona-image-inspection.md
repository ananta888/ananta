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
immutable storage using the existing artifact store; tenant/project-scoped asset
registry and revocation; separately authorized image selection/publication. Audio,
voice-model and video asset admission are separate profiles, not implicitly
accepted by this image-only port. The running ASR reference image has not yet
been rebuilt to include this new image-inspection component.
