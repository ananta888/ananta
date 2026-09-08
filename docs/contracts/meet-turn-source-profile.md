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

## Installed image and actual GPU check

The full image built successfully from source `7aa5cb789`:
`sha256:59096f02fa9515afa8cbbaab377d305ea44cfa6fd696a4361c61c384f2b5488b`.
Its package/runtime lock check passed. The first test was started after the
image ID became visible but before the build client had returned: Docker's
cold container creation exceeded 25 s, then completed asynchronously after the
fixture's initial cleanup inspection. Only a later `create` event existed;
the profile script had not run. The exact UUID-labelled, never-started test
container was inspected and removed. No other container was removed. A completed
build is required before the gate; an image ID alone is insufficient readiness.

The fixture now separates creation and execution into bounded phases and emits
only their closed names on timeout. Its in-container execution limit remains
15 s; uncertain creation/cleanup is explicitly a failure, not a clean result.
After the build completed, the original check passed in 8.93 s. The updated
two-phase check also passed, verifying installed source hashes, all six variants
and four pre-execution capture/profile denials without source/network/GPU mounts.

The first actual GPU turn returned `meet_worker_unavailable` after its original
60-second budget; that response did not identify the failed internal phase.
Do not claim a diagnosed or fixed cold-start problem. The component fixture now
performs its existing bounded pinned-model preload, as the real dialog fixture
already does, before creating the unchanged 60-second request. Preload checks
the exact model digest and positive GPU allocation, not just process health.

Both final packaged checks passed together in **58.95 s**: profile 8.754 s, GPU
case 49.525 s including fixtures. Model preload took **19.59 s**. The actual
Qwen/Piper-CUDA/NVENC turn produced **13 output tokens, 65,792 non-silent PCM
samples and 61,439 MP4 bytes**. No CPU/cloud fallback, human capture, source-code
mount, remote receiver claim or production evidence was substituted. These are
synthetic-policy technical observations of genuine GPU execution. Cold-start
performance and broader dialog/public rollout remain separate MAP-30/31 work.

## MAP-03 criterion audit

| Criterion | Implemented boundary |
| --- | --- |
| Explicit trusted source classes | Frozen dialog and turn classifiers share the source vocabulary, retain admitted image/video inputs, derive only from installed fixed handlers and Hub-owned fields, and are checked before dispatch/authority/rendering. Caller-selected classes remain forbidden. |
| Headless own sources, separate human authority | The installed dialog owns a new offline browser and synthetic/pinned persona sources. Neither profile permits human capture, personal cookies/profile paths or an implicit source/URL fallback. Human capture remains inaccessible, not automatically authorized by the probe or a machine join. |
| Independent rights | Separate chat read/send, audio receive and screen/speech/avatar publication capabilities; generated preview is not publication. Recording, training and tools are absent/denied, not inherited from room membership or artwork. |
| Concrete negative threat cases | The companion source-profile threat matrix maps tenant/project, SSRF/navigation, untrusted media/chat, compromised peers, replay/generation and signing-key loss to explicit denial seams and their existing negative tests. |

The final combined source-profile/client regression passed **330 tests in
218.00 s**, including all 208 cases from the earlier cleanup-failing run, with
no teardown errors. Together with the installed image and actual GPU checks,
this closes MAP-03's classification/threat-model criteria.
It does not close arbitrary browser privacy, multimodal injection, delivery
quality, native adapter support, cold-start performance or public release.
