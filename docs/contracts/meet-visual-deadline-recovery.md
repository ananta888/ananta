# Visual child cleanup after callback or Worker loss

MAP-11 source audit after MAP-25: `meet_visual_receive` children have an original
20-second deadline and normal parent/next-job cleanup, but are missing from the
independent Hub deadline scanner. If callbacks stop and no subsequent source
job is admitted, such a child can remain in progress even after its parent is
settled. Neither a successful frame analysis nor browser cleanup repairs that
persisted task state.

Add the visual kind to the existing Hub-owned keyset scanner and SQL task CAS.
Reuse the strict source-job parser at its captured issue time solely to validate
the persisted original deadline; this must not reauthorize an expired job.
Require an exact child ID, closed execution projection, parent dispatch and
runtime, then settle only the unchanged task at/after its original deadline.
Emit the content-free `meet_visual_deadline_expired` event once. Do not create a
new task, invent a Worker result, move deadlines, redispatch or retain images.

Before implementation, reproduce the omission through the real Hub TaskQueue
and SQL scanner, then check malformed projections, changed assignments, already
terminal children, a live parent, and a newly constructed scanner. Preserve
parent/audio/browser behavior and bounded cursor fairness. A fresh scanner is
not a full Hub-process restart or production recovery claim.

SOLID: move per-kind deadline binding validation into narrow strategy functions
registered beside their context keys. The generic scanner retains only paging,
clock, shutdown and CAS coordination. Keep its public `KINDS` and
`original_deadline` entry points compatible. This removes an expanding
kind-specific conditional from the scheduler rather than introducing visual
execution or policy authority into it.
