# Local Hub machine-key provisioning (MAP-05)

## Source audit and implementation plan

At Ananta `4f52b1214`, `scripts/setup_meet_media.py` combines Worker model
downloads and optional Hub identity creation. Its machine-key function reads
existing private/public paths without file-type or byte bounds; a FIFO can
hold provisioning indefinitely. It also creates the two files separately,
so an interrupted install leaves an incomplete pair which cannot be resumed.
The runtime signer already has a bounded private-file loader, but this
provisioning path does not reuse it. No key-only headless CLI exists.

Implement a separate, explicit local Hub identity provisioning adapter and
CLI. No models, Worker credentials, network calls, grants, runtime activation
or implicit trust publication belong in it. Preserve the old machine-key
function as a thin compatibility facade; ordinary voice setup is unchanged.

- Require an explicit absolute, private, operator-owned directory. Bind all
  file operations to its checked directory descriptor; reject symlinked
  provisioning directories and competing writers with a bounded result.
- Reuse bounded key-file admission for existing Ed25519 private keys and
  exact public-key comparison. Preserve normal read-only runtime secret
  mount behavior; provisioning itself does not follow target-file symlinks.
- Install only exclusive, complete, fsynced files without overwriting an
  existing key. An interrupted private-only pair may finish its derived
  public half; a public-only or mismatched pair fails without replacement.
- Return fixed machine-readable success/blocked codes and a public-key
  fingerprint only. No private material, parser details, paths or exception
  tracebacks in CLI output. No prompt, password request or download.
- Exercise actual Ed25519 signatures, repeat invocation, incomplete pairs,
  permissions, symlinks, FIFO/size/type rejection, contention, interrupted
  writes and descriptor/temporary-file cleanup in owned test directories.
  Reproduce the existing FIFO hang with a parent-bounded subprocess first.

The file adapter, key provisioning policy and CLI remain distinct (SRP/DIP).
This is local key preparation, not a production release, automatic approval
of room/Task scopes or complete operator deployment automation. Existing
Hub preauthorization, Meet public trust and their independent validation
remain mandatory. Only test-owned directories are used for implementation
checks; existing operator credentials and running services stay unchanged.
