# Exact resumable erasure of retired video bundles

`create_video_erasure_service` composes the existing Hub-owned erasure
lifecycle with two fixed file profiles: v1 `clip.mp4` (video, at most
1,500,000 bytes) and v1 `preview.png` (image, at most 350,000 bytes).
The existing image adapter retains its old API and exact `image.png` profile.
No external request selects an arbitrary filename or deletion root.

The caller must supply the dedicated video catalog and current project policy.
No public video API or retention schedule is activated by this composition.
Policy checks remain mandatory and repeat across both files. Only retired
bundles can enter `purging`; state/revision/audit fencing precedes any unlink.
After interruption, retry uses the exact purging revision and cannot reactivate
the asset. Completed deletion leaves the durable `purged` tombstone.

Each operation opens the private store and artifact directory with descriptor-
relative no-follow access. It accepts only a regular, single-link file with
matching size/hash and rechecks inode, device, size, modification time and
link count before unlink. Symlinks, hardlinks, changed bytes or foreign kinds
are rejected. There is no recursive removal and no cleanup of sibling files.
The directories remain. Missing exact files are accepted for recovery, and
their parent directory is fsynced. Transient fsync/I/O failures are classified
retryable, including a failure after an unlink already succeeded.

The private store must remain Hub-owned and inaccessible to untrusted writers.
The existing filesystem ownership assumption is preserved: a userspace stat
check and unlink are not an atomic compare-and-delete against an adversarial
local filesystem writer. This is ordinary file removal, not secure wiping of
storage blocks, memory, snapshots, backups or retained audit metadata.

SRP/DIP: the descriptor-bound file remover is shared, fixed media profiles are
small adapters, and the existing Hub lifecycle owns policy/state decisions.
Video supplies an explicit two-part selector rather than impersonating an
image bundle. No worker gets a Hub volume or creates its own cleanup queue.

Tests delete only their disposable generated fixtures, using explicit headless
policy doubles and Registry-issued test-only identities. They cover all member
states, interrupted second-file removal, symlink/hardlink/content changes,
stale revisions, revoked membership, fixed profiles and post-unlink fsync
recovery. No real project files or public configuration are removed.
The combined video/image erasure, retention, catalog and storage run passed
82 tests in 58.33 seconds.
