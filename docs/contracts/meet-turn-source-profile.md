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
