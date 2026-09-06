# Preview-filtered image discovery

`POST /api/persona-media/v1/projects/{project}/images/query` accepts exactly
`{"cursor": null, "limit": 20}` (or the returned cursor for the next page).
It requires user authentication and current project READ authority. Worker/service
credentials cannot list user assets. The closed response contains `items`
(normalized image references), `next_cursor` and `purpose: "preview"`.

Catalog SQL filters tenant/project and active state before scanning. Every
candidate is resolved through the same active-catalog, provenance/inspection and
current preview-policy adapter as direct image selection. Pending, retired,
corrupt or unauthorized assets are not returned. No generic artifact listing,
original upload, source/license document, storage path or image bytes are exposed.
Image references preserve synthetic/test-only classification.

Limits are 20 returned references, 64 candidate checks and a 3-second scan-loop
budget per page. Individual infrastructure operations still need their outer
request limits. A page can be empty while another scan segment remains. The
caller can continue using the opaque handle; no denied asset ID is included in
it. New assets preceding the current sort position require a fresh first-page
query; this is forward paging, not a release/evidence snapshot.

## Cursor boundary

Cursors are 256-bit random paging handles, not grants or evidence identities.
Only their hashes are stored, together with the exact tenant/project/subject,
scan position and five-minute expiry. They cannot be used under another scope or
after expiry. At most 128 unexpired handles per reader/project are admitted.
A scoped SQL lock serializes issuance across Hub replicas; no process-local
authority state or new shared encryption key is needed. Expired handles for the
current reader are deleted during issuance. The small per-reader lock row remains;
this is not a claim of complete historical metadata erasure.

Cursor bookkeeping is the only read-operation write. It changes no asset,
membership, source evidence, profile or permission. Project access is rechecked
after scanning. Listing is an access-checked metadata snapshot; selecting,
previewing or publishing an item always requires its own fresh checks.

## UI and structure

The dedicated `PersonaImagePickerComponent` uses the existing Hub-authenticated
client and parent profile facade. It replaces result pages (no unbounded list),
clears references/cursors on context changes, retains direct ID input as an
alternative, and revalidates a chosen image before loading its private preview.
It never starts Meet, human capture or publication.

Policy, catalog scanning, preview-reference checks and cursor persistence have
narrow injected ports (ISP/DIP). SQL pagination and image admission remain
separate responsibilities (SRP); browser code does not receive internal cursor
positions or private source records.
