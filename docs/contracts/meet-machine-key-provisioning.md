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

## Implemented local workflow

On the Linux Hub/operator host, explicitly prepare a dedicated key directory:

```bash
.venv/bin/python -m scripts.provision_meet_machine_keys /absolute/private/meet-hub-keys
```

The command returns a closed `ananta.meet-machine-key-provisioning.v1`
JSON receipt. Success is exit 0 with a SHA-256 fingerprint of the public
Ed25519 SPKI DER, `trust_activated: false` and
`production_release_evidence: false`. Failure is exit 2 with the fixed
`meet_machine_key_provisioning_blocked` code, not a traceback or a prompt.
Neither output contains key contents or paths.

Only `machine-private.pem` and `machine-public.pem` are installed, mode 0600,
inside the private owner-checked directory. The private file belongs only
to the Hub signer. The public half may be copied into separately authorized
Meet trust configuration; this command does not do so or reload a service.
An existing mode-0400 private key and mode-0644 public key remain readable.
Provisioning rejects file/directory symlinks; the runtime signer separately
retains its checked read-only secret-mount symlink compatibility.

The adapter holds a nonblocking directory lock, publishes complete fsynced
temporary inodes through exclusive hard links, and never replaces a target.
A private-only interrupted pair resumes by deriving its matching public half;
public-only, mismatched, unsafe or malformed input fails unchanged. Errors
after publishing one complete file can be retried explicitly without
generating another identity. A changed directory descriptor is rejected.
Application bounds cover file type, size and lock contention, not an operating
system or storage device that itself stops responding.

The existing `setup_meet_media.py --machine-keys` path delegates to this
adapter for compatibility. Its voice/Worker setup remains separate behavior;
the new key-only CLI performs no model download or Worker credential setup.

Verification: both historical private/public FIFO hangs reproduced under a
two-second parent limit (two failed regressions, 12.12 seconds total). After
the fix, 77 key/provisioning/signature checks passed in 35.82 seconds. These
cover real signatures, exact repeat invocation, partial-pair recovery,
permissions, oversize/symlinks, competing writers, write/fsync/link failures,
exclusive publication, directory replacement, descriptor cleanup and actual
bounded CLI success/failure. No operator key or live trust was changed.

The final combined principal, provisioning, authority, phase, route and
test-isolation regression passed all 400 checks in 142.53 seconds. Ruff and
the 77-file Worker packaging boundary check also pass.
