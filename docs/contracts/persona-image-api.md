# Headless persona image admission

This additive image-only surface implements the static PNG/JPEG part of MAP-18.
It does not grant Meet publication, capture a browser, clone a voice, accept video
assets or implicitly clear any other task in the track.

## Execution and authority

`PersonaAssetService` owns admission and storage; `HubPersonaInspectionTasks`
reserves an actual Registry run before creating/delegating an existing Hub task.
`HttpPersonaImageWorker` pins its configured destination to a private container
address and sends only the closed assignment, source bytes and MIME. No user
URL, provider credentials or human approval step is accepted.

The separate image worker has one execution slot and a durable SQLite lease
replay fence. It calls the signed Hub `/internal/image-lease` endpoint before,
during and after its supervised child. The callback checks the current task,
exact assignment, input digest, admission, project membership and policy. It
never inherits a tenant-admin override: the initiating subject must retain
explicit project membership. A callback cannot renew or create a task.

The assigned deadline is at most 20 seconds, decoding at most five seconds;
incoming HTTP connections have four bounded slots and a 25-second total cap.
Bodies are capped at 7 MiB inbound / 8 MiB outbound, with bounded incremental
reads. Request/result signatures have separate image/lease domains; results
bind the exact request digest. Fresh callback nonces prevent accepting an old
signed `allowed` response for a new authority check. Redirects and proxies are
disabled. Missing/unavailable authority fails closed.

Shared wire validation contains no decoder, Flask, model or GPU dependency.
Decoder, transport, task persistence, policy and composition remain separate
(SRP/DIP). The pre-existing media-turn HTTP adapter is unchanged; this new
image endpoint does not broaden its publication lease or reuse its key.

## API

All paths below are under `/api/persona-media/v1`. User operations require a
Hub user JWT and current project authorization. Service/worker credentials are
not user-policy authority. JSON fields are closed; query overrides are rejected.

| Method/path | Request | Result |
| --- | --- | --- |
| `PUT /projects/{project}/image-policy` | `policy` (`PersonaImagePolicy`), `expected_revision` | New immutable policy revision; requires MANAGE |
| `DELETE /projects/{project}/image-policy/{source_id}` | `expected_revision` | Revoked policy with consumed revision |
| `POST /projects/{project}/images` | `content` (base64), `media_type`, `origin_binding`, `license_binding`, `consent_binding` (explicit null allowed) | Verified active image/preview metadata, catalog revision 2 |
| `GET /projects/{project}/images/{artifact_id}/preview` | No body | Private normalized PNG; never publication |
| `DELETE /projects/{project}/images/{artifact_id}` | `expected_revision` | Terminal catalog revocation; requires MANAGE |
| `POST /internal/image-lease` | Signed `assignment` and fresh `nonce` | Request-bound signed boolean; no JWT substitution |

Policy source/license/consent pins must already resolve to immutable Registry
records, not caller-invented IDs. The source bytes must match the admitted
content digest. Inspect/store/preview/publish are independent purposes. No
public `publish` download route exists. Every asset read also checks its pinned
completed run, current policy and stored byte hashes. Old metadata without a
registered inspection run remains unverified. Synthetic visual content is not
the same concept as synthetic **evidence**: test/synthetic evidence never
promotes an asset into production eligibility.

Responses use `no-store`, `nosniff` and `no-referrer`. They contain no original
image bytes, raw storage paths or secrets. Invalid/missing objects and denied
authority produce bounded machine-readable errors, never an interactive wait.

## Optional deployment

`docker-compose.persona-images.yml` defines a private, internal-network CPU
worker: no host ports, no GPU, no models or Meet publishing keys, non-root UID,
read-only root, 256 MiB memory, one CPU, 32 PIDs and 64 MiB temporary storage.
Provision a dedicated private `worker-key` (32–256 bytes, mode 0600) and writable
`worker-state` beneath an absolute `PERSONA_IMAGE_STATE_DIR`. Do not reuse the
Meet publication key. The intended project name is `ananta-persona-images`.

The separate `docker-compose.persona-images-hub.yml` overlay connects the Hub.
Set `ANANTA_PERSONA_IMAGES_ENABLED=1` only with the exact repository revision,
execution-profile digest and environment digest in the named
`ANANTA_PERSONA_IMAGE_*` variables. These bindings are required, not generated
after execution. Configure actual source policy through the MANAGE API. Empty
or missing policy authorizes nothing. No public Hub/Meet deployment was changed
as part of implementing this optional overlay.

## Verification and remaining work

The combined image/task/asset/policy/Registry/Meet regression passed **210 tests
in 58.58 seconds**. It includes real HTTP Hub callbacks and worker child execution,
real project membership and source/run records, immutable disk/catalog storage,
preview-vs-publish denial, policy revocation, durable replay fencing and negative
wire-contract cases. Inputs and evidence are explicitly test-classified. No
human is required and no production release gate is claimed.

Physical purge/retention of revoked bytes, fencing of already-running publishers,
profile/UI selection and audio/video asset profiles are still separate unfinished
work. Revocation currently denies reads durably; it is not physical erasure.
