# MAP-09 task model completion audit

This audit covers the four existing MAP-09 criteria only. It does not complete
the persona/media track, production approval, general recovery, multi-agent
fairness or the separate intermittent decoder-startup investigation.

| Criterion | Implemented boundary and verification |
| --- | --- |
| Hub-owned start/join/publish/stop/leave with idempotent commands | Ordinary Hub TaskQueue plus current role admission; optional durable keyed HTTP starts, Worker dispatch single-winner SQL fence, one-shot browser operations, per-source CAS, and bound cancellation. No second scheduler. Start receipt replay never re-admits or restarts a terminal run. |
| Persistent allowed phases/revisions and bounded retry | Closed optional metadata in the same Task aggregate, with pure transition validation and full-context CAS; queued/admitted/connecting, verified joined, explicitly observed publishing, stopping and terminal precedence. Fixed conflicts replace uncertain redispatch. Existing Worker join/renew/leave and Hub request deadlines are unchanged. |
| Current organization/team/role state; no resurrection | Existing current parent/topology/role-assignment gates remain before dispatch and on every authority read. New shared terminal Task write policy rejects old runtime reactivation, force, rename pivots and legacy archive promotion. New attempts require a new Hub Task. |
| Viewing a project/persona/UI never auto-joins | Explicit start with selected capabilities; old Task status and new bodyless phase read/observe only query metadata. No polling, implicit source activation or human prerequisite for headless policies/tests. |

## Final technical checks (2026-09-08)

301 distinct combined Hub/Meet/Task regressions passed in 113.92 seconds. They
include real isolated SQL TaskQueue/CAS, phase transition/replay, terminal/force/
archive/rename cases, current authority and legacy status/assignment compatibility.
Ruff and the 77-file standalone Worker import boundary pass.

An additional focused acceptance set passed 70 tests in 35.11 seconds, including
real separate-process dispatch admission/restart, durable start repository and
coordinator cases, bounded browser operations and source/control exchange.

The final actual private Hub/Worker/Meet browser, with the terminal fence included,
passed in 39.18 seconds. It exchanged two correlated chat replies and moving
screen frames, then recorded publishing -> joined -> publishing -> cancelled at
phase revisions 5/10/19/21. Source pause/resume converged in 538.23/1162.30 ms;
reconstructed Hub ports retained cancellation and denied further observation.
The gate verifies registered resumed sources, not resumed decoded delivery.

The original general-Task reactivation defect was reproduced before its fix
(one failure in 7.55 s); the unchanged negative assertion passes after the fence.
The first phase integration failure was a new optional archived-field access bug,
also fixed and covered by actual TaskDB tests. Neither is disguised as a policy
waiver or a successful retry of a flaky assertion.

All local browser policy/model identities are synthetic technical observations,
not production release evidence. The companion includes parallel development
through `38b2821`: integrated check 639 frontend/586 Node passes (three skips),
followed by the later source-lease merge's Go suite and 20 focused Node/browser
passes. Public trust, serving dist and operator deployment were not modified.
