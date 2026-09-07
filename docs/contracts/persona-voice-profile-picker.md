# Persona voice profile picker

## Source check and scope

The Hub already admits canonical catalog voice descriptors under independent
voice policy and registered inspection receipts, exposes authenticated bounded
voice queries/references, and resolves them in organization/team/agent profiles.
Live Meet now negotiates and selects those profiles separately from images.
The organization profile editor still preserves an existing voice unchanged
but cannot discover, replace, inherit or disable one through its UI. This is
the next MAP-19 slice; metadata APIs remain the fully headless operating path.

## Implementation boundaries

- Add an independent voice picker using the existing shared form primitives.
  Query only `/voices/query`, at most 20 references per page. Replace pages;
  keep opaque cursors in the request body, bound to current Hub/project/owner.
- Selecting a listed entry or typing an existing asset ID must perform a fresh
  `/voices/<id>/reference` read before the draft can be saved. Validate exact
  kind, revision, scope, classification, hash and closed response shape.
- Expose missing/inherit/disabled/asset states. Saving uses the existing profile
  revision CAS, preserving unrelated image/video/style fields. It never changes
  an active Meet selection, synthesizes audio, downloads models or publishes.
- Show metadata/classification only. Do not offer uploaded model paths, URLs,
  arbitrary speaker IDs, cloning input or automatic audible previews.
- Clear draft/page state and cancel pending reads on owner or topology context
  changes. Apply bounded request timeouts; failed checks do not create a saved
  reference or retry a mutation automatically.

## Structure and verification

Generalize the small visual draft/page state holders to media-asset state by
composition, preserving old exports as compatibility aliases. Shared closed
reference/page validation may serve video and voice through fixed kind adapters;
do not weaken existing video validation or silently change image API behavior.
The profile facade still coordinates one editor's owner-scoped request lifetime;
its growing media-specific surface is a preserved SRP/ISP limitation. Keep new
validation and picker presentation outside it; avoid another independent
orchestration loop or broad UI rewrite in this slice.

Headless tests cover voice-specific HTTP routing, malformed/mixed-scope pages,
stale responses, fresh reference checks, inherited/disabled choices, save CAS,
timeout diagnostics and no audio/Meet side effects. Re-run existing image/video
profile tests and Meet picker tests, targeted lint and Angular compilation.
These checks are technical observations, not production release evidence.

## Implemented and verified

The editor now exposes independent voice discovery, fresh reference selection,
missing/inherit/disabled states and profile save CAS. Its shared draft/page
holders have media-appropriate names with compatible visual exports; closed
video/voice response validation uses fixed-kind adapters, preserving old video
behavior and leaving image transport unchanged. Metadata calls have a bounded
10-second timeout. Neither lookup nor save synthesizes audio or changes a live
Meet selection. Existing voice pins survive unrelated image edits.

All 168 combined Meet/persona UI tests passed in 2.07 seconds, including exact
voice routes, extra-field/foreign-scope/duplicate-page rejection, pending-read
cancellation, fresh selection, inheritance/disable, timeout and no-media side
effects. Targeted ESLint and Angular compilation passed; the existing unrelated
KnowledgeHygienePage RouterLink warning remains. Admission/policy setup and
actual selected-voice GPU delivery remain separate, not implied by this UI.
