# Semantic media/speech TODO migration

The four legacy planning tracks were consolidated into
`todos/archiv/todo.ai-snake-semantic-media-speech-program.json`. Its
`legacy_task_coverage` object is the canonical static migration matrix. It
contains exactly the frozen 66 legacy IDs: `semrtc-001..020`,
`speechslow-001..010`, `peerspeechsync-001..012` and `semspeech-001..024`.
Every value is a non-empty list of new `ASMP-*` task IDs.

Multiple targets are intentional decomposition, not duplicate ownership. The new programme assigns one authoritative layer per concern:

| Concern formerly repeated | Authoritative new layer |
|---|---|
| Alignment and transcript resolution | SPR owns realtime alignment; SYN owns peer evidence resolution; OFF consumes both offline |
| Relay and transport | TRN owns encrypted data transport; SFU owns media fanout only |
| Dataset building | DAT owns evidence lifecycle and immutable manifests; SYN/OFF may request admission but do not build datasets independently |
| Evaluation | VIS, SPR and ML own domain metrics; QA aggregates release evidence only |
| Negotiation | CTL owns compute contracts and leases; UI tasks only project Hub state |

No legacy item was discarded as a non-goal. The programme's explicit non-goals remain product boundaries rather than dropped work. The matrix does not claim implementation: initial new-task states are `todo`, and later state changes require acceptance evidence under `docs/planning-pipeline.md`. `legacy_task_coverage_status=verified_by_ASMP-BASE-003` attests only that this static mapping is complete; it never freezes or implies the current implementation status of any target task.
