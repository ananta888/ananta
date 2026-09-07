# Authenticated video admission API

The existing `/api/persona-media/v1` blueprint includes a separate video child
blueprint. User operations require normal user authentication and current
Hub-owned project/source policy; machine credentials cannot substitute. The
private video lease callback remains HMAC-only.

Paths are relative to `/api/persona-media/v1/projects/{project}`:

| Method | Path | Purpose |
| --- | --- | --- |
| PUT | `/video-policy` | Install explicit video policy under expected revision |
| DELETE | `/video-policy/{source_id}` | Revoke source policy |
| POST | `/videos` | Delegate inspection, verify receipt, admit private bundle |
| GET | `/videos/{artifact_id}/preview` | Authorized PNG preview, never MP4 publication |
| DELETE | `/videos/{artifact_id}` | Revoke both parts under exact catalog revision |
| GET | `/videos/{artifact_id}/purge` | Read bounded erasure/resume state |
| POST | `/videos/{artifact_id}/purge` | Erase the exact retired bundle |

Upload has exactly `content` (base64), `media_type` (`video/mp4`),
`origin_binding`, `license_binding` and nullable `consent_binding`. Bindings
must reference identities already admitted by the Hub Registry; matching an
`SRC_` pattern is not evidence. Caller-supplied tenant, owner, inspection IDs,
output hashes, URLs and extra fields are rejected. Decoded input is capped at
3,500,000 bytes and the complete JSON body at 4,700,000 bytes. Admission returns
closed asset metadata, `state: active` and catalog revision 2, not storage paths.

Policy writes contain `policy` and `expected_revision`; `media_kind: video`
and matching project are mandatory. Destructive actions contain exactly
`expected_revision`; booleans, numeric strings and stale revisions are denied.
Purge is separate from revocation and reports `secure_device_erasure: false`.

No public clip `publish`, `download` or `content` route exists. Publication
remains a separately authorized service operation requiring profile/publisher
integration. Query parameters and Transfer-Encoding are rejected. Responses
inherit no-store, no-referrer and nosniff headers. The shared user JSON boundary
rejects duplicate keys for image, video, profile and retention routes without
changing their field schemas.

## Independent Hub configuration

`ANANTA_PERSONA_VIDEOS_ENABLED=1` is the exact Hub-only opt-in. It is independent
of `ANANTA_PERSONA_IMAGES_ENABLED`; video does not implicitly enable image or
profile services, background deletion or any source policy. Also required:

- `ANANTA_PERSONA_VIDEO_KEY_FILE`: private inspection key file;
- `ANANTA_PERSONA_VIDEO_WORKER_URL`: private `/v1/persona-videos` endpoint;
- `ANANTA_PERSONA_VIDEO_REPOSITORY_REVISION`: immutable repository commit;
- `ANANTA_PERSONA_VIDEO_EXECUTION_PROFILE_DIGEST`: exact profile hash;
- `ANANTA_PERSONA_VIDEO_ENVIRONMENT_DIGEST`: actual admitted runtime binding.

Missing/invalid enabled configuration fails closed, without image credentials,
provider fallback or Hub decoding. Syntax validation of configuration values
does not constitute production evidence. `docker-compose.persona-videos-hub.yml`
mounts only the video key and attaches the named external private video network;
it publishes no port and leaves the feature disabled by default. Pair it with
the worker compose from [persona-video-http.md](persona-video-http.md).
No public Hub/Meet/Caddy restart or policy activation is performed.

SRP/DIP: the child blueprint translates authenticated requests/responses; a
separate composition module wires policy, task, receipt, catalog, storage and
erasure ports. The existing larger image composition still groups image,
profile and retention wiring (a preserved SRP limitation); video wiring is
not added to that mixed block.

Tests cover authenticated scope, closed payloads, kind separation, revision
guards, absent publication/download escapes, duplicate keys, disabled/non-Hub
use and isolated bootstrap failures. The private CPU gate executes upload,
preview, revoke and purge through these routes with an explicit JWT-validation
fixture but real membership, policy, Hub tasks, Registry, FFmpeg HTTP worker and
filesystem. This remains synthetic test evidence, not a production token,
release grant or live Meet delivery claim.

The combined real CPU HTTP/API, bootstrap, image routes, profile/query and
retention regression passed 97 tests in 72.93 seconds. No rollout was performed.
