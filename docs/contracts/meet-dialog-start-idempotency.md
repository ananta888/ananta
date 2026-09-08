# Explicit durable Meet start idempotency (MAP-09)

Source audit at `a4b5dde1e`: Worker dispatch replay is fenced, but each repeated
HTTP start constructs a new task, lease and runtime. An HTTP client retry can
therefore create a second legitimate but unintended participant. Do not infer
that the existing Worker replay fence makes the HTTP operation idempotent.

Add an optional `Idempotency-Key` header to both existing dialog-start routes.
Requests without it retain their exact legacy behavior. A separate Hub start
coordinator rechecks current project/parent write access before looking up any
receipt, hashes the complete tenant/project/owner/parent/key scope, and binds
the key to the exact canonical bounded start payload. SQL uniqueness elects
one caller across Hub processes. Only that caller may invoke the existing Hub
dialog service; the coordinator never delegates to Workers itself.

Completed retries return the original start receipt, not a fresh grant or an
assertion that the original session is still connecting. Current state remains
available through the existing status endpoint. Changed payloads conflict;
pending, failed and uncertain outcomes return bounded machine-readable errors,
never an implicit redispatch. A crash does not release the claim. Durable small
receipts have no automatic expiry/reuse; task/receipt retention policy remains
separate and must not silently turn an old retry into a new session. No raw
idempotency key, media, chat, room invite, grant or exception text is persisted.
These keys/digests are command replay metadata, not SRC/RUN identities.

Use a narrow SQL claim/read/finish port and the existing start/access ports
(SRP/DIP/ISP); leave the broad dialog service unchanged. Preserve role/lifecycle
checks, headless policies, start response and Worker wire. Cover actual parallel
SQL claims, restart replay, payload and scope isolation, current permission
loss, pending/crash/failure/uncertain storage, exact terminal CAS, and optional
HTTP compatibility. Verify one real Hub task/one Worker dispatch for sequential
and concurrent duplicate starts. No trust, serving deployment or production
evidence claim. Persisted lifecycle phases and general recovery remain open.
