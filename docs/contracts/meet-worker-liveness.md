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
successful liveness is not idle capacity or model/GPU readiness. The Compose
template enables that probe and an explicit configurable positive CPU quota
with a four-CPU default. No host port, wider network, capability or mount is
added. An unhealthy status must not become Hub task reassignment authority.

Test real HTTP liveness during an existing executor lock, rejection of remote
and malformed requests, no executor/signing calls, probe failure/bounds and
the rendered Compose resource/security contract. Keep probe/policy separate
from the existing broad server composition (SRP/ISP); run the Worker import
boundary check. Do not restart a running Worker as part of template edits.
