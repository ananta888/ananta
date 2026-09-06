# Delegated Meet dialog runtime (implementation round)

This work is not deployed or production-accepted. Local tests are synthetic
technical observations, not Hub Evidence Registry release records. The combined
browser/ASR/Direct/SFU/TURN/long-session gate is still required.

Work paused at the user's request on 2026-09-07. The short cross-repository
composition gate passed; the five-minute soak did **not** finish successfully.
It observed moving remote pixels through 236 seconds and four lease generations,
then a browser runtime failure. The two-hour gate has not run. A subsequent
Meet expiry-timer fix still needs long-session verification. Do not promote
these observations to production readiness or resume rollout automatically.

## Authority and composition

The Hub creates an ordinary `meet_dialog_session` TaskQueue task. Its immutable
tenant/project, owner, room binding, dispatch lease, runtime, Hub session,
capabilities and two-hour deadline are revalidated before every Meet signature.
The operator must separately enable `ANANTA_MEET_DIALOG_ENABLED=1` and provide
`ANANTA_MEET_DIALOG_POLICIES`, a closed array of objects containing `tenant_id`,
`project_id`, and `capabilities`. Existing publish-only allowed scopes do not
grant receive rights. The existing machine signer stays on the Hub; no private
signing key enters the Worker or Meet. The Worker separately defaults disabled
with `MEET_DIALOG_ENABLED=0`.

Human APIs under `/api/meet/v1`:

- `POST /projects/<project>/dialogs` (or `/tasks/<task>/dialogs` beneath that
  project): `capabilities`, `duration_seconds` (30–7200), `chat_mode`
  (`off`, `mention`, `direct_question`, `room`) and optional `audio_mode`
  (`off`, `transcribe`, `dialog`; default off).
- `GET /projects/<project>/dialogs/<task_id>`: content-free task status.
- `DELETE` on the same path: owner-authorized cancellation. Expired operational
  authority is never required merely to stop a task.
- `GET /projects/<project>/dialogs?cursor=0`: bounded, owner-filtered task list.
- `PATCH /projects/<project>/dialogs/<task_id>`: CAS `expected_revision` and
  explicit `chat`, `audio`, `screen` booleans. Each source has its own revision
  and activation timestamp; pausing screen does not revive an old chat reply.

The attached project's Angular Meet panel exposes start, list, complete stop and
these independent source controls. New source selections default off; unassigned
capabilities cannot be enabled. Closing the panel does not cancel the server task.

The private Worker accepts closed HMAC-signed `/v1/dialogs` assignments. It
executes at most two isolated sessions and remembers consumed dispatch leases
across restarts. This is an execution limit, not a global Meet room limit or a
second task scheduler. Its operator-configured `MEET_HUB_DIALOG_URL` must point
exactly to `/api/meet/v1/internal/dialog`. No caller-provided callback or redirect
is followed. Requests and responses use distinct signature domains; responses
bind the exact request bytes and nonce. Bodies are bounded at 16 KiB.

## Current-state backchannel

The Hub calls Meet's `/api/machine/sessions/authorization` over TLS with a fresh
single-use v2 grant. The response binds the exact machine identity/session,
current membership epoch, receive revision, publisher grants and active audio
publication epochs. Worker flags cannot replace this response. The backchannel
contains metadata only, never chat, PCM, frame keys, SDP or ICE.

Meet's `ms_…` session lease, the Hub's dialog dispatch lease, and each ASR child
task lease are distinct. The Worker adapter explicitly maps them and checks all
source/session fields; a receive revision is never renamed `publication_epoch`.
Repeated source announcements retain their epoch, while stop/restart or source
replacement creates a new epoch owned by Meet's registry.

Fresh 120-second grants renew the same browser membership. The Worker polls
current Hub authority every two seconds; a bounded failed request stops the
browser. This does not promise instant server-side revocation of a previously
issued grant against a compromised Worker: it remains bounded by that grant's
expiry. No automatic reconnect or takeover of an expired task exists.

## Chat and audio execution

Only newly received, consented human chat enters the bounded browser queue.
The Hub revalidates both authorities, reserves the existing at-most-once reply
budget, and delegates an ordinary media task to the existing Worker transport.
Replies are sent only to a delivered, still-current input; an uncertain send is
never retried as a new answer. LLM work runs concurrently with the browser's
lease monitoring, not as Worker-to-Worker orchestration.

Both typed-chat and audio-derived replies now use one injected
`MeetDialogReplies` composition with the existing Hub SQL media-capacity gate
and exact configured speech profile. Production bootstrap refuses an enabled
dialog without these budgets. Waiting/revoked/uncertain execution follows the
same bounded media admission rules as other turns; capacity denial never
silently dispatches or retries. The low-level ASR capture child remains separate
from this media-reply capacity pool; host-wide shared GPU/ASR scheduling is not
claimed by this wiring change. The combined dialog/reply/capacity/speech tests
passed 69 tests in 50.70 seconds, including actual SQL admission with a synthetic
worker result and negative bootstrap/dispatch checks. This extraction preserves
SRP/DIP by sharing one reply composition across both input sources.

Audio requires separate `audio_mode` activation as well as `audio.receive`,
`chat.send` and the publisher's current source grant. The Hub reserves one
source-bound `meet_audio_receive` child at a time through parent-task CAS, with
720 reservations per dialog at most. Each job captures ten seconds of actual
16 kHz mono PCM and has at most thirty seconds for capture, local ASR and optional
answer generation. The existing pinned local CUDA ASR profile performs the
recognition; unavailable assets/hardware do not cause downloads or cloud fallback.
Raw PCM never crosses into the Hub. The authenticated transcript is transient,
not task content or a log. `transcribe` does not broadcast the transcript; `dialog`
applies the bounded answer policy. This bounded utterance adapter is not a promise
of continuous, gap-free transcription or production barge-in performance.

## Agent-owned screen

The current source adapter creates a separate ephemeral offline Chromium
workspace owned by this Hub assignment. Source identity `screen:<hub-session>`
is derived from the signed session context, not from an arbitrary caller tab ID.
CDP supplies continuously changing JPEG frames with a one-frame latest-only
buffer. Meet publishes its own synthetic canvas separately from camera/audio:
640×360, 5 FPS, 256 KiB encoded frame budget, one decode in flight, one-second
decode limit, two-second stale-frame stop and thirty-second source activations.
Renewal/membership changes invalidate old source generations. SFrame transport
and ACK requirements remain unchanged.

This adapter currently shows a trusted task-owned activity view, **not arbitrary
web navigation, human tabs, a host desktop or authenticated web content**.
Its displayed activity is restricted to actual local chat/reply/audio execution
states; prompts, replies, tokens and room identifiers are not rendered.
Network access, navigation, extra pages, downloads and unknown embedded content
revoke the source; a heuristic secret mask is not treated as authority. Existing
Camofox/Browser-Use live-view requests remain unsupported until exact owned-page
admission is connected. Independent pause/resume controls are connected; arbitrary
page/source switching, receiver-side secret-marker tests and the complete shared
acceptance matrix remain open.

## Structure and remaining work

Authority, task persistence, cryptographic HTTP envelopes, audio admission and
local source adapters are separate ports. Separate chat, audio and screen pumps
own their queues and lifecycles; the runtime composes fresh Hub state and cleanup.
Existing media transport's dependency
on Worker encoding helpers is retained DIP debt; move shared wire helpers into
`ananta_contracts` when that adapter is next generalized.

Current short tests cover real Worker HTTP signatures, task/policy mutation,
actual test-database parent/ASR TaskQueue CAS, metadata-only completion, source
fencing and byte/timeline/epoch mappings. They do not prove decoded remote media,
GPU throughput, NAT traversal, production trust or the two-hour soak.

The Meet direct-browser gate now additionally verifies signed synthetic human
and machine identities over local TLS, explicit publisher consent, correlated
chat, 16,000 actual PCM samples with a nonzero signal, decoded moving screen
pixels, three renewals and receive revocation. It caught two integration bugs:
empty Opus startup frames wrongly poisoning the transform, and a Chromium
remote-audio clone requiring a silent playout sink to produce real PCM. Empty
frames are dropped, not forwarded; the sink owns only a clone, has zero volume
and is destroyed on close. Neither change permits a cleartext fallback.

`MEET_CROSS_REPOSITORY_GATE=1 .venv/bin/python -m pytest -q
tests/test_meet_dialog_cross_repository.py` is the separate real-wire composition
gate. It uses the adjacent built Meet repo, its own loopback TLS identity and a
three-minute, read-only Docker TCP forwarder for privileged port 443. No host
sysctl, existing container, production trust or certificate is changed. Its
model-result and project-policy ports remain explicitly synthetic; this is not
a GPU/ASR or production-release claim. The local image used by the TCP forwarder
can be selected with `MEET_TEST_PROXY_IMAGE` and is resolved to an immutable ID.

`MEET_DIALOG_SOAK_SECONDS=300` (or 7200 for the full bounded run) extends this
opt-in gate and its own proxy lifetime only. It checks real lease renewals,
repeated decoded screen movement, periodic newly consented chat replies and
process-tree limits (less than 3 GiB RSS and 80 processes). Startup and a five-
second stop reserve are explicitly outside the active observation window.
The requested duration alone is never evidence of successful completion.
