# Bounded Hub signing-key loading (MAP-05)

Source audit at `f1e7eb0b7`: `MeetMachineGrantIssuer` checks path permissions then
calls `Path.read_bytes()`. It does not require a regular file or bound bytes,
and permission checking and reading address separate path resolutions. A private
mode-0600 FIFO can block startup indefinitely; a changed target can bypass the
earlier permission check. The key remains correctly Hub-owned, never Worker-owned.

Extract a small local key-loading adapter from grant issuance (SRP/DIP). Require
a nonempty regular file with existing private permission rules and a 4 KiB limit.
Use a nonblocking, close-on-exec read descriptor and validate its actual identity,
type, permissions and metadata before/after a bounded read. Reject target or
content changes without retry; close every descriptor. Preserve symlinked regular
secret mounts if the opened target matches the checked immutable snapshot.
Reject FIFO/socket/device/directory/oversize and malformed/encrypted/wrong-key-type
inputs with fixed errors, never path/key/parser details. No private data enters
logs, grants or Worker projections. Disk-kernel stalls are not represented as a
guaranteed application-level timeout; the bound concerns accepted file types and
bytes, including never blocking on a FIFO open/read.

First reproduce the old FIFO hang in an owned subprocess with a hard parent
timeout. Test actual Ed25519 key/signature, normal and symlink secret files,
permissions, special files, size, swapped target, metadata/content mutation,
read errors and descriptor cleanup. Re-run existing private grant/browser paths.
No operator files, trust, credentials, deployment or active services are changed.
This does not complete versioned Trust profiles, key rotation or organization/
agent principals; those broader MAP-05 requirements stay open.

## Verification (2026-09-08)

The old FIFO constructor timed out in the owned two-second subprocess regression
(one failure, 9.12 s including the application fixture). The new loader rejects
the FIFO before reading and admits only a bounded regular-file descriptor.
The same FIFO assertion is part of the final 82-test key/grant/media regression,
which passed in 36.84 seconds. Tests also cover real Ed25519 signatures, 0400/0600
files, symlinks, group/other permissions, socket/directory/missing/empty/oversize,
encrypted/malformed/wrong-type keys, swapped regular/FIFO/permission targets,
short/error reads, in-place changes, invalid paths and exact descriptor closure.
The issuer accepts a narrow injected loading function and retains its original
v1/v2 claims and current Hub authorization checks.

The actual private Hub/Worker/Meet phase browser passed in 33.47 seconds with
the new loader, real signed grants, moving screen, two correlated chat replies,
source pause/resume and durable cancellation. Policy and model output remain
synthetic, not production release evidence. Ruff and Worker boundaries pass.
No live credential file, operator trust, deployment or running service changed.
