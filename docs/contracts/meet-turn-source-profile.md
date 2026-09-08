# Fixed one-shot media source profile (MAP-03)

Source audit `b3620b3b7`: dialog v1 already derives and persists its installed
handler's source classification. The separate `meet-turn.v1` renderer still
generates local TTS and either a procedural avatar, admitted persona image or
admitted persona video without an explicit stored classification. Its closed
request, asset admission, model profile and publication grant remain mandatory.

Add a separate immutable turn-profile compiler in `ananta_contracts`, reusing
only the common denied-operation names. It describes generated speech, generated
video and any admitted image/video input. A persona clip must be classified as
an admitted `persona_video` input, not silently labelled synthetic footage.
Generation is distinct from publication: only a turn carrying the existing
Hub-issued meeting assignment has the installed v1 publication upper bound.
Neither profile nor source class is a new caller/Worker wire field.

The Hub persists the derived projection and explicit publication intent for new
media tasks; capacity/current and successful terminal checks compare the exact
projection against the original delegated turn. The existing v1 lease callback
validates present projections against stored fixed handler inputs before allowing
rendering/publication work. Missing legacy projections retain only the old fixed
handler behavior; malformed present projections fail closed. Failed/cancelled
cleanup must remain possible after a profile mismatch. Profile metadata is not
asset provenance, identity, a Meet grant or authority to record/receive/capture.

The installed Worker compiles the same profile before model/TTS/render work;
it cannot select another capture runtime. Model text stays untrusted user data.
Existing asset/lease/codec checks and v1 assignment fields remain unchanged.

Verify neutral/image/video and preview/publication combinations, immutable copy
semantics, denied capture/tool/training/receive escalation, incompatible visual
inputs, actual TaskQueue persistence, tampering before capacity/success and lease
callbacks, preserved failure cleanup and legacy compatibility. Run focused
media/persona/chat/capacity regressions and an updated packaged runtime check.
No GPU inference, receiver delivery or production-release claim follows from
classification unit tests. Arbitrary browser-workspace privacy/streaming remains
MAP-13/15/16, not implemented by adding a source label.

SRP keeps classification independent of render execution and Hub persistence;
DIP shares a pure contract without importing Hub services into the Worker.
Existing broad `HubMediaTasks` and local runtime composition are preserved debt;
put profile derivation/validation in focused helpers, not another renderer branch
or a plugin registry that could expand permissions implicitly.

## Implementation

`TurnSourceProfile` is a separate frozen value, not an inherited renderer or
policy plugin. Six variants distinguish neutral/image/video inputs and preview/
publication. The input list keeps admitted persona clips explicit. Fresh nested
projection objects cannot mutate its source bounds or one another.

`meet_turn_source_binding` owns the Hub metadata and exact-context check. A
partial or malformed new binding is denied; legacy absence of both new fields
retains the old renderer. Capacity admission and successful terminal CAS compare
against the original turn, including coherent preview-to-publication expansion.
The lease callback checks the stored projection but still needs all existing
scope, task, parent, profile and asset rights. Terminal failure cleanup is not
blocked by corrupted source metadata. This is fixed-code classification, not
remote attestation of a compromised endpoint or cryptographic immutability of
the entire Hub database.

The first 96 classifier/real Hub task/media/child-fence checks passed in 67.07 s.
The expanded regression and installed-image checks remain in progress. The
standalone boundary scan covers 80 files and remains free of Hub imports.

The expanded set passed all 208 test bodies in 144.66 s but exposed two cleanup
errors in the existing capacity bootstrap test: its temporary global database
replacement outlived the test body and reached the autouse isolation guard.
The fixture now scopes that replacement explicitly to bootstrap configuration
and restores it before runtime cleanup. The guard remains unchanged; this is
not a production database migration or a source-profile test failure. Repeat
the affected bootstrap/isolation cases and the final regression before release.

The corrected bootstrap plus all 34 source-profile cases passed together:
40 passed in 31.60 s, no cleanup errors. The earlier 208-case set also included
the real Hub/Meet role-principal interoperability check and existing image/video,
chat and capacity regressions. The next check uses a newly built immutable
Worker image, never a source mount substituted for installed code.
