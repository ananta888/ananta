# ANANTA Meet integration

## Contract and scope

Ananta owns the tenant/project/task association and rechecks current project
membership on every request. Meet independently owns room admission, OIDC,
device proofs, one-time signaling tickets and media. An association **does not
grant Meet membership or prove that a room exists**.

The initial version deliberately uses `meet_ui_then_attach`:

1. Open **Raum in Meet erstellen** in the project or task Meeting panel.
2. Authenticate using Meet's existing Keycloak flow; create a private room.
3. Copy its invitation into the Ananta panel and choose **Raum zuordnen**.
4. Authorized project readers can open the associated meeting. Task bindings
   additionally require the existing task-read authority, including
   Organization/ownership fences where applicable.

This explicit copy-and-attach flow needs no cross-origin token handoff or new
Meet API. It is also fully automatable through existing APIs/browser actions;
manual operation is a UI option, not a requirement of the test runner.
One-click cross-app provisioning is **not implemented**. A future automation
adapter must use an explicit Meet authorization contract, never Hub service
credentials as a substitute for Meet OIDC.

### Capability matrix

| Capability | Version 1 |
| --- | --- |
| Create meeting | Existing Meet UI in a separate tab |
| Persist association | Tenant/project/optional task + configured provider origin |
| Open meeting | Canonical Meet invite; Meet still checks user/device |
| Unlink | Hub association only; no room deletion or revocation |
| Service health | Bounded `/healthz` and `/config` probe |
| Room existence/participants | Unverified; not inferred from global health |
| Pair-Dev / artifact / LiveKit migration | None |
| SFrame/LiveKit protocol bridge | None |

The new port, SQL adapter, application service, Flask adapter and UI have
separate responsibilities (SRP). The application service depends on the
existing project access port and a small meeting store contract (DIP/ISP).
No existing media implementation or worker orchestration is changed. Existing
large bootstrap/UI composition files remain existing composition points; no
additional business logic is added to them.

## Deployment and rollback

The integration is disabled by default. Explicit Hub configuration:

```text
ANANTA_MEET_ENABLED=1
ANANTA_MEET_ORIGIN=https://webrtc.ananta.de
```

The origin must be canonical HTTPS, without credentials, a port, a path,
query or fragment. Only this operator-configured destination is used. Requests
cannot supply an upstream URL. The provider probe verifies TLS, follows no
redirects, sends no Hub credentials, does not read `.netrc` or proxy credentials,
and caps response bytes and read/connect time. Its `available` status checks
the required auth/E2EE configuration, not actual media interoperability.

Configuration must be passed into the Hub container explicitly by the operator's
deployment override. The additive `docker-compose.meet.yml` overlay provides
these two variables for the existing `ai-agent-hub` service and defaults to off;
append it with `-f` to the selected deployment's existing Compose file list.
It does not configure workers, restart Meet or modify proxy networks.
The existing local Compose/Caddy edits are intentionally
not changed by this integration. Rebuild/restart only the relevant application
services after checking running work; changing repository files does not prove
that a running image contains the feature.

On enabled Hub startup, the SQL adapter creates the additive `meet_bindings`
and `meet_binding_events` tables in the existing configured Hub database.
They support SQLite and PostgreSQL through SQLAlchemy. Keep the database on the
Hub's persistent volume; no worker-shared filesystem is required. A startup DDL
failure fails activation rather than falling back to ephemeral storage.
Changing the configured origin isolates the previous origin's associations;
old room identifiers are never silently sent to a new provider.

Rollback: disable `ANANTA_MEET_ENABLED` and redeploy the relevant application
services. Preserve both tables. Meet and existing Pair/LiveKit services remain
independent; reverting the flag does not delete rooms or associations.

## API and permissions

All endpoints require a **Hub-issued user JWT**. Meet/OIDC JWTs and worker
service tokens are not accepted as substitutes. The runtime is Hub-only.

```text
GET|PUT|DELETE /api/meet/v1/projects/{project}/binding
GET|PUT|DELETE /api/meet/v1/projects/{project}/tasks/{task}/binding
GET            /api/meet/v1/projects/{project}/health
```

Read/open requires current project READ authority. Attach/unlink requires WRITE
authority. Archived projects and mismatched tenant/task/project bindings are
rejected. User-token project restrictions remain in force. Responses use
`Cache-Control: no-store`; the frontend clears links on context/account changes
or request failure.

PUT has exactly `invite_url` and integer `expected_revision`; DELETE has only
`expected_revision`. The initial revision is zero. Writes use compare-and-swap;
409 means reload before retry. Repeating the same desired association under
the current revision is a no-op. Tombstones preserve the revision after unlink
so a stale request cannot resurrect an old link. Each actual write records the
actor, action and revision in the same SQL transaction, without logging invite
URLs, room codes, media or tokens.

## Privacy and limitations

A room invitation is a bearer capability. Associating it intentionally shares
that capability with the authorized readers of this context. Do not associate
a room whose existing invite must remain private from those readers. Database
access and backups must be protected accordingly. Unlink and project access
revocation cannot invalidate invitations already copied by another user;
room-level revocation is a separate Meet responsibility.

No camera, microphone or screen capture occurs in Ananta. Media controls remain
in Meet. External links use `noopener noreferrer`; there is no iframe, token
forwarding, browser secret store or direct worker-to-Meet policy channel.

## Automated verification

Deterministic backend and UI checks do not require credentials, interaction or
a live provider:

```bash
.venv/bin/pytest tests/test_meet_integration.py tests/test_project_lifecycle_service.py tests/contracts/test_public_rendezvous_compose.py -q
cd frontend-angular
npx vitest run src/app/features/meet src/app/features/projects/project-management.component.spec.ts src/app/components/task-detail.component.spec.ts
npx tsc --noEmit -p tsconfig.app.json
```

Real browser preflights from `frontend-angular/`:

```bash
node scripts/meet-live-gate.mjs
MEET_LIVE_LOCAL=1 node scripts/meet-live-gate.mjs
```

The local mode maps only the Meet hostname to `127.0.0.1` inside Chromium,
preserving HTTPS hostname/certificate verification. It tests the local Caddy
edge without editing DNS or `/etc/hosts`. The public mode also verifies
Ananta's `/info` rendezvous routing. Both check that page load does not invoke
capture and unauthenticated room creation is denied. Neither proves a working
authenticated media session or independently external NAT traversal.

The opt-in positive gate requires two different **explicitly authorized test
accounts**, an enabled/deployed Ananta Hub, and an otherwise unused test project.
Provide an operator-owned, non-symlink mode-0600 JSON secret file containing
`username`, `password`, `username2`, `password2`, `hub_token` and `project_id`.
Do not commit it or paste it into logs. Run:

```bash
MEET_LIVE_FULL=1 MEET_LIVE_SECRET_FILE=/absolute/path/to/meet-test-credentials.json node scripts/meet-live-gate.mjs
```

Optional `MEET_LIVE_HUB_ORIGIN` defaults to `http://127.0.0.1:5000`; remote Hubs
must use HTTPS. Combine with `MEET_LIVE_LOCAL=1` for local routing. The runner
uses real browser PKCE login, separate browser devices, a new private test room,
Hub association/read/unlink, DataChannel chat and synthetic microphone audio.
It checks decoded samples and active SFrame, then leaves the room and removes
only its own exact-revision association. Meet's normal directory retention may
keep the empty private test-room metadata temporarily; no global deletion or
cleanup of other rooms is attempted.

The entire run has a 180-second deadline. Missing credentials return exit 2
with `meet_live_test_credentials_missing`, not an interactive login wait or a
passing skip. Errors report only the bounded stage name. TURN remains separately
`unverified` unless a dedicated forced-relay gate proves it. Synthetic media and
test identities never count as production release evidence. The positive
runner itself remains unverified until executed with those credentials.
