# Persona selection before dialog dispatch (MAP-20)

## Source audit and bounded plan

At `9039b55d9`, bounded one-shot media turns resolve profiles before dispatch,
but a long-lived dialog starts with neutral/configured defaults and accepts
explicit image/video/voice selection only after Task creation. There is also
no direct test of the same profile in two simultaneous video sessions. Existing
multi-publisher browser tests cover different synthetic personas.

Add an optional closed `initial_persona` start command with independent avatar
and voice choices. Each choice contains a pinned profile, never an asset URL,
bytes, provider command or extra capability. Image/video/voice selections require
their existing explicit negotiations; video repeat policy remains explicit.
Omission preserves the complete legacy request and assignment behavior.

A dedicated Hub initial-persona resolver reuses the current independent profile
ports, resolves exact admitted references before Task creation and rechecks
them immediately before dispatch. It does not hydrate media, activate sources,
schedule work or choose a fallback. Failed admission creates no Task; revocation
after Task creation fails that exact dispatch and does not try another profile.
The dialog coordinator only composes this service (SRP/DIP).

Persist and dispatch a closed versioned initial projection with exact asset
references and selection digests, separate from mutable live selections.
Bind it into original preauthorization/phase identity and generic Task write
protection. The Worker validates it under the exact assignment but never uses
historical metadata as current publication authority: fresh Hub selection,
source revision, Meet lease and current asset policy are still required.

Reuse existing feature-local passive profile pickers for optional initial
choices. Changing scope or disabling an output discards its pending selection;
there is no automatic download, playback, join or approval.

Verification: closed input/negotiation and legacy contracts, real Hub Task
creation/revocation races and original-projection immutability, two same-profile
Hub sessions with independent selection/cancellation, independent Worker slots,
and actual private simultaneous video publications in separate browser contexts.
Test-only media/policy remain synthetic technical observations, never production
evidence. No public trust, serving build, model or human profile changes.

## Implemented boundary

The optional request shape is:

```json
{
  "initial_persona": {
    "avatar": {
      "mode": "persona-video-v1",
      "profile": {"organization_id": "example", "owner_kind": "organization", "owner_id": "example", "selection_digest": "<exact current profile hash>"},
      "repeat_mode": "loop"
    },
    "voice": {"profile": {"organization_id": "example", "owner_kind": "organization", "owner_id": "example", "selection_digest": "<exact current profile hash>"}}
  }
}
```

This is a shape example, not a valid admitted profile or complete start request.
At least one of `avatar` and `voice` is required. Image mode is
`persona-image-v1` and forbids `repeat_mode`; video requires `loop` or
`hold_last`. The normal start capability/negotiation fields remain mandatory.
Malformed profile input returns a closed HTTP 400 code without input echoes,
Task creation, grant issuance or Worker contact.

`MeetDialogInitialPersona` resolves copied pins through small independent
profile ports, before Task creation, and rechecks after Task persistence and
immediately before dispatch. The Worker receives only
`ananta.meet-initial-persona.v1`: output mode, immutable asset reference,
selection digest and video repeat policy. Private profile ownership metadata
stays in the Hub. The historical projection cannot be removed, retrofitted,
coerced (`true`/`1`/`1.0`) or rewritten through generic Task persistence.

The original assignment digest and phase binding include the initial pins;
later explicit source CAS commands do not rewrite them. Current asset and
role checks still gate every preparation/publication. A revoked profile blocks
its source and pending result; it does not select a neutral replacement.
Only an explicit authorized neutral selection can do that.

The existing broad dialog service remains a composition coordinator; it is
not further expanded with asset decoding, profile lookup implementation or UI
state (preserved SRP boundary/debt). The dedicated resolver and passive UI
component separate admission and presentation. A shared immutable reference
validator removes duplicate image/video metadata checks without changing
legacy errors or accepted formats (SRP/OCP); independent profile ports protect
DIP/ISP and make revocation races testable. There is no Worker orchestration,
implicit publication, shared mutable media pool or broadened implementation
contract (LSP/side-effect review).

## Real private verification, 2026-09-08

- `test_meet_dialog_avatar_video_browser.py` passed in 63.04 s with the initial
  video profile bound before dispatch. Four actual generations covered
  video/image/video changes, pause/resume and asset revocation. Remote pause
  776.71 ms; local/remote revocation 253.09/295.21 ms. Both complete spoken
  outputs contained 220500 samples and were correlated with actual received
  audio; screen continued, human captures and transform errors were zero.
- Meet `machine-same-persona-video.browser.test.js` passed both Chromium and
  Firefox receiver cases in 9.33 s. Two separate machine contexts used the
  identical normalized synthetic clip and independent task/runtime/session
  IDs, both decoded red/blue phases, independent screens and simultaneous
  fresh speech. First-source image replacement and stop did not change the
  second generation; its frames advanced. No human capture or transform
  errors. The initial test incorrectly opened a second avatar without closing
  its own generation; `meet_avatar_busy` correctly rejected it. The fixture
  now follows the production close/open contract, without relaxing that fence.
- Forty focused initial-admission/contract tests passed in 31.73 s, including
  real HTTP malformed-profile handling and two same-profile Hub Task sessions.
  Twenty-two admission/Worker-isolation tests passed in 20.65 s. A test-only
  overbroad browser-close assertion was corrected to distinguish the second
  pump's own startup cleanup from stopping the first pump.
- All 283 Meet/persona UI tests passed in 3.55 s. Angular template compilation
  and targeted ESLint passed; compilation retained the pre-existing unrelated
  KnowledgeHygiene unused RouterLink warning.

These are synthetic policy/media tests with actual private transport. The Hub
browser gate ran Python from current source and used the previous immutable
Worker image only as a browser host; it is not verification of an installed
new Worker package, GPU generation, forced TURN, public rollout or production
release evidence. Expanded regression, packaged verification and task closure
are recorded separately when completed.

The expanded two-process focused backend run passed **503 tests in 189.41 s**,
including real SQL organization/project revocation for image, voice and video,
legacy negotiation/transport, live source revisions, immutable Task metadata,
preauthorization and phases. After normalizing malformed persisted projection
errors to bounded domain denials, **80 tests passed in 70.00 s**. The added
persistence cases use actual Hub Tasks plus a private SQL preauthorization
ledger: original pins survive live neutral selection and a restarted phase
reader; generic saves cannot rewrite them, and cancellation remains valid.
