# Independent Meet source control acceptance

MAP-12 source audit at c126c4056/18ff3034f, after the visual-control UI fix:

| Required boundary | Implementation and checks |
| --- | --- |
| Independent source lifecycle | Hub `meet_dialog_controls` keeps per-source revisions; Worker screen, speech, avatar and receive pumps have separate ownership and idempotent cleanup. Avatar image/video and voice selection advance only their bound source. Public-browser replacement uses its own child Task. |
| Hub emergency stop | The owner-authorized DELETE cancels the exact Hub dialog Task through the existing bound terminal CAS. Current-authority callbacks then fail; the Worker closes all its sources. This is a per-Task emergency stop, not an unauthenticated global process-kill API. Parent/project/role revocation is independently revalidated. |
| Meet revocation and no revival | Current membership, lease, receive/source and control revisions fence cached results. Renewal invalidates old pumps/publications. Known terminal failures cannot enter the transient control-read retry path. The independent parent watchdog removes stalled runtime/browser processes. |
| Key, decoder and buffer release | SFrame context destruction wipes keys and pending outputs; receive pumps clear owned frames/PCM and close decoders, while source-generation checks reject late results. Replay and missing keys never return plaintext. |
| Content-free audit | Native Task history, phase snapshots and the signed append-only terminal diagnostic ledger expose bounded status/revision/reason/resource observations. Diagnostic admission cannot mutate a terminal Task or its execution authority. No raw media, SDP, token or invite is added. |
| Optional UI | Exact optional controls, owner-filtered Task list, AI label, source selectors and whole-Task stop. Visual-only Tasks no longer invalidate the list. Opening the UI never starts media or grants consent. The same Hub APIs work headlessly. |

Previously recorded actual private checks include speech-only pause and parent
cancellation during non-silent reception (91.31 s), packaged independent
avatar/screen lifecycle and terminal observations, installed camera/screen
receive revoke/regrant (104.99 s), and the real stalled-runtime watchdog
(2139.86 ms cleanup with nine tracked descendants). Their precise scope is in
`meet-dialog-speech-interruption.md`, `meet-dialog-terminal-diagnostics.md`,
`meet-screen-encryption-startup.md` and `meet-dialog-progress-watchdog.md`.
They are synthetic-policy technical observations, not production release IDs.

Before closing this task, rerun the focused current-source matrix for legacy
and optional control CAS, speech/avatar/browser cleanup and replacement,
receive revocation, native lifecycle/terminal fencing and diagnostic integrity.
The new visual UI has already passed its 267-case Meet/auth batch and isolated
optimized build. The broader public/TURN, two-hour soak, automatic room rejoin,
fair speaking policy and all-tests milestones remain separate open tasks; do
not absorb them into a claim that every feature is finished.

SOLID review: policy stays Hub-owned, each source owns its lifecycle, terminal
observability is separate from execution status, and adapters preserve the old
wire format. The large dialog composer/component remain existing SRP debt;
this audit adds no new orchestration responsibility to either Worker or UI.
