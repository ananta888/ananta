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
