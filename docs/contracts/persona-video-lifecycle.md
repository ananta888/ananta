# Hub-owned video asset lifecycle

`PersonaVideoAssetService` composes the existing admission/read/revocation
lifecycle with a small video metadata format. It requires a video-aware policy
port before any task, lookup or revocation. There is no image-policy fallback.
MP4 inspection is delegated through normal Hub tasks and the exact registered
completion receipt is checked before reserving any artifact records.

The Hub creates two private immutable references: normalized silent MP4 and
PNG preview. These are artifact IDs, not new evidence identities. Their
metadata retains the Registry-issued source/run identities and exact
assignment, dispatch lease, frame count, hashes and sizes. The source content
is not decoded or stored as an untrusted original on the Hub.

The existing SQL reservation starts at revision 1 (`pending`). Exact private
storage writes run under the catalog's revision guard and repeated current
policy checks; activation is revision 2. A failed write or authorization check
retains a durable revoked reservation, including partially written files,
for explicit bounded erasure. It does not silently delete the audit record or
expose an orphan through the generic artifact API.

Reads authorize the current user/project and requested purpose before catalog
lookup. `preview` returns only the PNG; `publish` requires its own permission
and returns the normalized silent clip. Current policy, immutable inspection
receipt, active catalog revision and stored bytes are rechecked before release.
Revocation consumes the exact supplied catalog revision and retires both parts.
Subsequent access fails; the separate erasure service can remove the exact
retired files. Already delivered bytes cannot be recalled, and filesystem
unlink does not claim secure device or backup erasure.

SRP/OCP/DIP: `PersonaAssetLifecycle` owns the shared lifecycle, the admission
format protocol owns only media-specific metadata construction, and injected
ports own policy, Hub tasks, catalog and bytes. Image APIs retain their existing
signatures and mutable dependency-injection seams. Existing broad policy ports
and storage transaction boundaries are preserved for compatibility; this
change does not claim a distributed atomic transaction across policy updates,
filesystem writes and catalog activation. Revalidation plus durable revocation
handles failures without broadening access.

The service tests use actual Hub tasks, Registry receipts, SQL catalog and
video policies with a clearly structural decoder double. They cover preview
versus publication, forged completion metadata, interrupted storage, revision
conflicts, policy revocation after activation/reading, denied lookups and
cross-kind inputs. The optional private CPU-container gate additionally uses
real project membership and FFmpeg over signed HTTP, persists the result,
reads each authorized part, revokes the bundle and erases only its own
disposable test files. Every identity in that gate remains test-scope.

The combined lifecycle, real CPU HTTP, image/video tasks, erasure, policy and
existing profile regression passed 115 tests in 87.37 seconds.

Authenticated user routes/bootstrap, automatic video retention and
persona-profile/Meet publisher selection remain separate integration work.
No public deployment or production policy is enabled by this service change.
