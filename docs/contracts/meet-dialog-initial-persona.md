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
