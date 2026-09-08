# Meet terminal Task write fence (MAP-09/11)

Source audit found a real remaining lifecycle defect: the general Task state
machine deliberately permits retry/promotion of failed or cancelled Tasks. That
generic behavior must not revive a Meet session's old dispatch/runtime identity.
An isolated real TaskQueue test reproduces cancelled -> in_progress through the
ordinary Task CAS (2026-09-08, 7.55 s failure); no production data was touched.

Add a small infrastructure-free Meet-session mutation policy to the existing
transactional Task repository write boundary. Do not change general Task retry
semantics. Once a meet_dialog_session is terminal, its status, kind and execution
identity cannot be changed or renamed into an active task through generic update,
status CAS, force or bulk calls through those ports. The SQL Task repository has
no independent update_many implementation. Metadata-only edits with the same terminal
identity remain compatible. A new dialog requires a new explicitly admitted Hub
Task and dispatch, never an automatic migration or replay of the old run.

Keep the policy separate from the already broad Task repository (SRP/OCP); invoke
it under the same authoritative row lock before commit, alongside existing write
policies. Reject with a fixed machine-readable reason. No new scheduler, no Worker
write authority, no attempt to stop/restart real services. Add pure status/identity
matrix and real SQL CAS/update/force/rename tests, plus general Task and current
Meet regressions. This is a necessary completion gate for persistent phase
terminal precedence, not a reason to relax or invent phase history.

Prevent the rename pivot in both directions on existing rows, including while
active: Meet -> ordinary -> retry -> Meet must not evade the terminal policy.
Archive restoration preserves terminal identity; a legacy archived Meet record
cannot be converted to todo on restore. Neither rule blocks ordinary Task retries.

## Verification (2026-09-08)

The original real SQL negative test now passes (8.11 s), retaining the cancelled
Task and denying old authority. An 81-test focused set passed in 39.76 seconds:
terminal/active status matrix, immutable terminal scope/context, forced CAS,
save/rename, metadata-only edit, terminal archive restore, rejected legacy archive
promotion, plus general Task admin/state-machine and knowledge-context regressions.
CAS returns a bounded false result; save/restore reject with the fixed
`meet_dialog_terminal_identity_immutable` reason, without changing the old row.
No new dispatch or runtime identity is minted by these checks.

The final combined phase/Meet/Task regression passed 301 distinct tests in
113.92 seconds, with two pytest workers. The shared Task files only compose the
small pure policy (seven added lines in the repository, three in archive restore).
The broader pre-existing repository remains SRP debt; this change does not expand
its domain logic or change ordinary Task retry rules.
