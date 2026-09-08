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
status CAS, force or update_many. Metadata-only edits with the same terminal
identity remain compatible. A new dialog requires a new explicitly admitted Hub
Task and dispatch, never an automatic migration or replay of the old run.

Keep the policy separate from the already broad Task repository (SRP/OCP); invoke
it under the same authoritative row lock before commit, alongside existing write
policies. Reject with a fixed machine-readable reason. No new scheduler, no Worker
write authority, no attempt to stop/restart real services. Add pure status/identity
matrix and real SQL CAS/update/force/rename tests, plus general Task and current
Meet regressions. This is a necessary completion gate for persistent phase
terminal precedence, not a reason to relax or invent phase history.
