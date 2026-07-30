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

Ananta intentionally ships an unavailable default provider and secret resolver.
An operator must install both as application extensions:

- `hub_git_authorization_provisioner`
- `hub_git_secret_resolver`

Until then health remains unavailable and onboarding fails closed. No provider
secret or endpoint is returned by list, detail, mutation or health responses.

## Lifecycle

- `GET /api/source-control/v1/git-authorizations`
- `GET /api/source-control/v1/git-authorizations/{authorization_ref}`
- `GET /api/source-control/v1/git-authorizations/health`
- `POST .../{authorization_ref}/actions/revoke`
- `POST .../{authorization_ref}/actions/scope-loss`

Lifecycle mutations require the returned ETag in `If-Match` plus an
`Idempotency-Key`. Revoke and scope-loss clear all granted scopes immediately.
