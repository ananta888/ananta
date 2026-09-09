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
