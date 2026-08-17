# Source Control Git authorization onboarding

Git authorization onboarding is owned by the Hub. Browser clients never submit
tokens, credential references, clone URLs, remote URLs, tenant IDs, project IDs
or owner IDs.

## Public request

`POST /api/source-control/v1/git-authorizations` accepts exactly:

- `authorization_handle`: an opaque handle previously issued by a server-side
  GitHub App, OAuth or Generic Git onboarding flow
- `authorization_kind`: `github_app`, `github_oauth` or `generic_git`
- `repository`: `owner/repository` for GitHub and `null` for Generic Git

The route requires an authenticated `admin` or `project_owner`,
`Idempotency-Key`, and an authenticated tenant/project/subject scope. Scope is
derived exclusively from the authenticated principal.

## Internal provider boundary

`HubGitAuthorizationProvisioningPort` exchanges the selection for:

- a stable opaque connection reference
- the provider-owned endpoint
- an opaque credential reference
- granted scopes
- provider authorization state

The Hub validates that result through `GitRemoteAccessPolicy`, requires
`contents:read` for GitHub or `repository:read` for Generic Git, and persists it
through `SQLHubGitAuthorizationRepository`. The repository supplies CAS,
immutable revision history and content-free audit records.

Ananta ships an unavailable default provider and secret resolver. Operators may
install both as application extensions, or enable the built-in GitHub adapter
with Hub-owned App credentials:

- `hub_git_authorization_provisioner`
- `hub_git_secret_resolver`
- `HUB_GIT_GITHUB_APP_ID`
- `HUB_GIT_GITHUB_APP_PRIVATE_KEY_REF` (opaque secret reference, never a token)

The adapter resolves a server-issued `github-installation:<id>` or stored
`github-oauth:<handle>` against GitHub, requires `contents:read`, and returns
only opaque credential references. Installation tokens are minted later by the
secret resolver and never persist in the authorization registration or Angular
payloads. OAuth still requires a prior server-side grant store; without one the
path stays fail-closed.

Until a provider is configured, health remains unavailable and onboarding fails
closed. No provider secret or endpoint is returned by list, detail, mutation or
health responses.

## Lifecycle

- `GET /api/source-control/v1/git-authorizations`
- `GET /api/source-control/v1/git-authorizations/{authorization_ref}`
- `GET /api/source-control/v1/git-authorizations/health`
- `POST .../{authorization_ref}/actions/revoke`
- `POST .../{authorization_ref}/actions/scope-loss`

Lifecycle mutations require the returned ETag in `If-Match` plus an
`Idempotency-Key`. Revoke and scope-loss clear all granted scopes immediately.

## Angular status

The Git authorization list, selection, health and lifecycle controls are
implemented in Angular. The browser continues to receive opaque authorization
references only. The current Angular verification completed with `68/68`, a
successful production build and `42/42` project-selector tests. The related
authorization production-fix suite completed with `147/147`.

Private GitHub App and OAuth remain external boundaries. No persistent private
provider registration, installation or OAuth grant is configured by the
repository default. Those capabilities therefore remain externally
unverified even though the Hub and Angular boundaries are implemented.

## Credential-free public remotes

Public repositories use a separate flow:

- `POST /api/source-control/v1/public-remotes/validate`
- `POST /api/source-control/v1/public-remotes`

The request uses structured public repository selection. It does not accept
browser-supplied clone URLs, credentials or credential references. Validation
returns a short-lived opaque handle; create consumes that handle through the
Hub-owned idempotent persistence path.

Local Compose activation is explicit opt-in. Production keeps
`ANANTA_SOURCE_CONTROL_PUBLIC_REMOTES_ENABLED=false` by default. Public remote
support remains `partial`: live validation returned `200` and resolved a
40-character commit, but no live public-remote create and no dedicated Angular
UI E2E were executed.
