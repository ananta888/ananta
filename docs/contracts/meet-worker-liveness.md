# Publisher Worker liveness and CPU budget (MAP-10)

Source audit at `ec048fd3d`: the standalone Worker has bounded authenticated
POST execution, an eight-connection HTTP limit, memory/process/tmpfs budgets
and a private container. Its image/Compose service has no healthcheck and
no explicit CPU quota. A healthcheck must not execute a model, reserve work,
renew a Hub lease, read private keys or advertise GPU/model readiness.

Add a content-free, loopback-only `GET /healthz` liveness response, separate
from the authenticated assignment endpoints. Reject non-loopback peers,
query/path variants, request bodies and transfer encodings without reading
them. Never sign health responses or include Task/room/credential details.
Keep POST authentication, execution and connection limits unchanged.

Add a small standard-library probe using only fixed loopback and the Worker
port, a bounded response and socket timeout. Docker supplies the independent
wall-clock timeout. Failure returns a nonzero exit without private details;
successful liveness is not idle capacity or model/GPU readiness. The image
provides the probe's Docker healthcheck; Compose inherits it and adds an
explicit configurable CPU quota with a four-CPU default. No host port, wider network, capability or mount is
added. An unhealthy status must not become Hub task reassignment authority.

Test real HTTP liveness during an existing executor lock, rejection of remote
and malformed requests, no executor/signing calls, probe failure/bounds and
the rendered Compose resource/security contract. Keep probe/policy separate
from the existing broad server composition (SRP/ISP); run the Worker import
boundary check. Do not restart a running Worker as part of template edits.

## Implemented verification

The standalone image declares a 15-second interval, three-second healthcheck
timeout, 15-second startup grace and three retries. Its standard-library
probe uses fixed `127.0.0.1:8094`, a two-second socket timeout, exact response
type/length/body and no credentials. Docker supplies the independent total
execution deadline. The route closes each connection and rejects duplicate
lengths, transfer encodings, body/path/query variants and non-loopback peers.
It neither signs responses nor calls Turn/Dialog execution.

The Compose service now defaults to `MEET_MEDIA_CPUS=4.0`; operators can set
an appropriate positive quota explicitly. RAM, PIDs, tmpfs, private networking,
non-root user, capability drop and sandbox security options remain unchanged.
The resolved Compose contract was also checked with a 2.5-CPU override.

The first 29 HTTP/probe/contract tests passed in 23.55 seconds. A real private
container gate passed in 14.00 seconds using the locally installed immutable
Worker image `sha256:0e112df54c364f1672890840dcf94986274bfd0c356fe2a1bbab4680ce9200bf`
and read-only mounts of the current Worker/shared-contract source. It ran
without GPU requests or network access, as non-root, with a read-only root,
0.5 CPU, 256 MiB and 32 PIDs. Docker reported healthy; health output was empty
and the actual replay database contained zero executed leases. The test
removed only its own label-matched temporary container.

The combined HTTP/media/failure/CPU/GPU-profile regression passed 145 tests
in 61.32 seconds. This does not execute GPU inference.

A fully fresh dependency build did not finish: offline construction lacked an
APT cache layer; the subsequent networked build reached its 600-second bound
during installation of the large CUDA wheels. Neither attempt is a successful
full Dockerfile build. Instead, a private source-packaged test image was built
from the installed immutable runtime above, with the actual current source
packages and the same Dockerfile healthcheck declaration. Its image identifier
is `sha256:37776310a9d0118753648924d3df76c4bf7ef8c2a2be721309c5610f07c3d8c3`.
It has no source bind mounts; a checked local temporary base tag was used
because BuildKit does not accept a bare local image ID as a `FROM` reference.

The packaged gate passed in 21.99 seconds: non-root, network/GPU-free, private
key only, no importable Hub package, exact health-module hash and zero executed
leases. Suspending only its owned listener made Docker report unhealthy;
the probe independently exited 1 with no output. Resuming that same listener
restored healthy without restart or Task execution. The first packaged run
failed a test assertion (14.12 seconds): Docker exposes `--tmpfs` configuration
under `HostConfig.Tmpfs`, not in its bind-mount list. The assertion now checks
both separately; no mount or security constraint was removed.

This verifies source packaging and health behavior in the installed runtime,
not a fully fresh dependency build, deployment, model readiness or aggregate
GPU isolation. Existing services/credentials were unchanged. The health-only
import guard scanned 79 files. The server's broad existing composition remains
SRP debt; health request policy and the probe are separate small modules.

## Full locked-build follow-up

The subsequent complete Dockerfile build, bounded to 1,800 seconds and using
the explicit Python inventory lock, finished successfully. Its immutable image
is `sha256:3444cb7d1124c52959be70043b4c66fa78bba0e55f109c18b724d2ab46dddb9e`,
built from source snapshot `76ec57d71`. Unlike the earlier source-packaging
check, this executes the dependency-install and build-inventory steps too.

The packaged health gate passed against this new image in 26.03 seconds,
without source mounts: healthy/unhealthy/healthy, silent probe output, same
listener PID, no restart and zero executed leases. The non-root, network/GPU-free
resource restrictions and exact health-module hash checks remained enabled.
This supersedes the pending full-build outcome, not the separate limitations
on GPU readiness, production deployment or aggregate resource isolation.
No existing service was replaced or restarted.
