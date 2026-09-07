# Private preview-only clip discovery

`POST /api/persona-media/v1/projects/<project>/videos/query` accepts the closed
JSON body `{ "cursor": null, "limit": 20 }`. Authentication is the existing
user/project boundary; Worker credentials or room membership are insufficient.
The response contains only `items` (closed video references), `next_cursor`
and `purpose: "preview"`. It has no raw bytes, storage paths, provenance IDs,
participant identities, download URL or publication permission. Responses are
private/no-store. Supplying a publish purpose or other fields fails.

The Hub requires project read access before looking up a cursor or scanning,
then checks each reference through `PersonaProfileVideos`: current video-only
policy, private active catalog entry and registered inspection receipt, with
another catalog revision check. Revoked, pending, foreign or unavailable clips
are omitted. The shared discovery service additionally requires exact media
kind, tenant, project and artifact identity from its reference port. The Hub
checks project access again before returning. A returned reference is only a
discovery snapshot; selection, preview and publication recheck current rights.

Pages contain at most twenty references. Each request scans at most 65 IDs,
checks at most 64 candidates and uses a three-second between-candidate budget.
An empty authorized page may still have a continuation; denied IDs never
appear in it. This budget cannot preempt a stalled database call: connection
and lock timeouts remain infrastructure responsibilities.

The opaque 43-character paging handle is bound to tenant/project/subject,
expires after five minutes, and is persisted only as a hash. Up to 128 handles
per reader scope are allowed, under an existing cross-Hub SQL lock pattern.
The new `persona_video_cursors` and `persona_video_cursor_scopes` tables are
separate from the unchanged image tables. Image and video tokens cannot be
exchanged even for the same user and project. No data migration or image
feature enablement is required by video-only composition.

Structure: the compatible image facade and new video composition share the
bounded discovery engine and cursor persistence (SRP/OCP/DIP), but inject
separate catalogs, permission/reference ports and cursor tables. The image
class names, constructor arguments, table layouts and `images` port remain
compatible. No Worker scheduling or policy decision moves to the browser.

Verification on 2026-09-07: 58 image/video discovery, bootstrap and route tests
passed in 44.41 seconds, including real private SQL assets and Hub Registry
test receipts, revocation, cross-kind/scope rejection and no media loading.
The structural video-inspection fixture is not a real decoder or production
release gate.
