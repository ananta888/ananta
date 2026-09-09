# Separately authorized visual reception

## Source audit and staged implementation plan (2026-09-09)

Ananta `7144b5645` and Meet `b23de52` have publication-bound audio reception,
not visual reception. Both capability lists and source/grant validators reject
camera/screen input for machine recipients. The existing semantic-compute Worker
has a small Pillow image-feature implementation, but its artifact-publishing
handler must not be reused as an implicit retention grant for live room media.

Add `video.receive` independently of audio, avatar and screen publication.
Meet's publisher-owned consent admits only explicitly selected current camera
or screen publication IDs. Extend the bounded grant set from two audio sources
to at most four audiovisual sources; per-source capability checks remain
mandatory on server policy and client key/subscription gates. Old clients and
servers may reject the new capability rather than silently ignoring it.

Expose a separate opt-in visual probe/subscription port. A subscription is bound
to original tenant/project/task/runtime/Hub session, actual Meet lease and
generation, room membership, publisher/publication/epoch, receive revision and
original deadline. It only samples a currently live authorized remote decoded
video track, never `getUserMedia`/`getDisplayMedia`, mixed room video, the Worker's
own output, a URL or a browser tab. Sampling is bounded in dimensions, encoded
bytes, frequency, count and outstanding operations. Revocation/track replacement/
lease change closes owned elements/bitmaps/canvas and stops subsequent delivery.
The encrypted transport and blind signaling-server model are unchanged; the
explicitly authorized AI endpoint is correctly described as able to decrypt.

The Hub creates ordinary bounded visual-analysis child Tasks under current
session/source authority. Only that assignment can consume the frame port.
Frames and keys stay on the Worker; Hub result callbacks contain only closed
task-authorized structured analysis, not raw media. Start with reusable bounded
image features and frame-change analysis in an isolated native child, extracting
the existing Pillow calculation into a narrow execution-only module (SRP/DIP).
Do not copy its artifact-storage policy, global decoder settings or Hub client.
Expose a narrow analysis port for additional explicitly provisioned local
semantic/OCR/model profiles; statistical frame features must not be mislabeled
as semantic scene understanding. No cloud fallback, arbitrary provider, media
storage, transcript persistence or training follows from receive permission.

Verify capability/source combinations and exact grant epochs before key delivery,
headless source selection, bounded decoded frame export, replay/late callbacks,
decoder failure and cancellation. Then exercise real isolated browsers and a
normal Hub Task/Worker analysis chain. Full MAP-25 closure still requires its
remaining live audio and visual acceptance; this document is a plan, not proof
of a capability already deployed to the public instance.

## Receive-policy implementation, 2026-09-09

The shared source-to-capability contract and Hub backchannel now admit at most
four exact audiovisual sources per publisher, each requiring its own audio or
visual receive capability. Visual grants cannot create ASR child tasks. The
unchanged legacy client probe additionally checks VP8 receive support when the
new capability is selected; actual frame consumption still needs the separate
visual probe. The focused capability/backchannel/audio regression passed 135
tests in 54.21s. This is technical test observation, not production evidence.

Pure capability mapping protects SRP/DIP. Existing broad Hub audio coordinator
and backchannel validators remain SRP debt; this slice only tightens their
source boundaries and does not add visual decoding or persistence to them.
Visual analysis Task/Worker integration is still pending.

## Reusable local image-feature execution

The semantic-compute image calculation now lives in execution-only
`worker.image_features`. Existing result shape/thumbnail statistics remain
compatible. Per-call pixel/dimension/format limits replace the previous global
`Image.MAX_IMAGE_PIXELS` mutation; metadata limits are checked before decoding.
The old artifact handler still owns its separate publish policy. Meet's image
includes only this narrow execution module, not the semantic artifact handler.
This removes hidden decoder side effects (SRP/LSP) without introducing Hub
imports, storage, capture or provider access to the Worker. Eighteen extraction
and existing semantic-worker tests passed in26.19s. The test cache emitted a
permission warning, not a test failure; later runs use a separate temporary cache.

## Shared receive identities and leases

Audio and visual adapters share the pure source-job identity validator and
execution-only `SourceLease`. The ASR contract and `AudioLease` compatibility
name/error remain intact. The lease now copies supplied binding/job values so
later caller mutation cannot silently change its accepted identity or deadline.
Revocation remains irreversible; neither a fresh timer nor an unchanged receipt
reopens a closed lease. This removes duplicated policy checks (SRP/OCP) without
worker-side task creation. Existing audio adapters and the new visual pump
passed33 tests in22.50s; the larger integration regression passed286 tests in
107.52s with one explicit runtime-inventory opt-in skip. The Worker boundary
audit passed all115 current Worker files.

## Closed native visual profile

`image-features-v1` accepts exactly three source-bound JPEG frames, each at most
640x360 and98,304 bytes. It reports only dimensions, average RGB and adjacent
mean-color differences. These statistics are explicitly not OCR, object
recognition or semantic scene understanding. No configurable model, URL,
prompt, provider, tool or artifact output is admitted by this profile.

The existing bounded subprocess runner invokes a separate native child with a
5s wall budget,3s CPU limit and512MiB address-space limit. The decoder checks
actual original dimensions before thumbnailing, so a declared small frame
cannot hide an oversized JPEG. Cancellation/current source authority are checked
before execution, while the child runs and before accepting its closed result.
The focused native/contract/audio regression passed130 tests in52.13s; subsequent
100 Hub/contract tests in44.40s also cover original-dimension rejection. One
actual local child executed the pinned statistical profile, not a GPU model.
Real packaged Worker/Meet source acceptance remains pending.

## Hub reservations and Worker pump

Visual reception is independently default-off and requires both `video.receive`
and an explicit, revision-bound Hub control. The Hub reserves an ordinary child
Task bound to its parent, assigned Worker, dispatch lease and exact publication
epoch. Completion keeps the original cooldown; source/control changes and parent
cancellation close the child. Shared repository mutation locks serialize the
final policy reread and completion against Hub cancellation/control updates.
The external Meet receipt remains a point-in-time check, not a distributed
transaction with the Meet server.

The Worker owns only a bounded subscription/pump and the native calculation.
It sends closed statistics to the Hub, never raw frames; neither frames nor
statistics are persisted in Task context or admitted as chat instructions.
Only negotiated visual authorization/control receipts gain a 64KiB bound for
the existing 19-publisher/four-source maximum; callbacks and legacy controls
retain 16KiB limits. Separate persistence, authority and execution modules
protect SRP/DIP. The existing broad dialog composition service remains SRP debt.

The final focused wiring gate passed36 tests in23.26s, including policy changes,
parent cancellation and unavailable locks at completion admission. Earlier
100 Hub/contract checks,33 Worker/audio checks and286 integration regressions
also passed (one explicit runtime-inventory opt-in skip). These are technical
observations. The next gate uses an immutable packaged Worker and real private
Meet browsers for separately granted camera/screen sources; no public deploy,
production trust or semantic scene understanding is claimed.

## Real packaged source acceptance

On2026-09-09 source `f223cbe53`, immutable Worker image
`sha256:bd4cc14c24a8dd4eaa4a4f0a2bc3f0a5fdfd18282d84c8a1567c6d4635afccd0`,
passed both actual camera/screen chains in66.58s. The extended repeated-grant/
active-revocation pair passed in104.85s. Each received three decoded frames in
the packaged Worker, ran the native statistical child and completed the exact
Hub child Task. A new source grant allowed a fresh bounded assignment; revoking
that active assignment failed it without accepting another result. Task context
contains neither raw frames nor calculated features. The default-off Hub control
was exercised independently of the publisher's grant.

The first setup attempt selected a missing default proxy image tag; explicitly
pinning the existing private proxy fixed setup. Regrant testing then exposed a
fixture UI race: clicking the same peer could inspect the old checked DOM before
Angular reset its editor. The helper now awaits the actual revoke receipt and
unchecked editor before a new checkbox action. No production policy was loosened.
The shared private fixture extraction subsequently passed the camera/regrant/
revocation chain again in57.23s; the four companion browser cases passed10.17s.

The fixture keeps ephemeral authentication, container lifecycle and source
scenario assertions separate (SRP). The GPU option is only for the next audio
gate; image statistics themselves do not need a GPU. These observations use
synthetic sources and private infrastructure, not a public deployment or
Hub-registered production release run.
