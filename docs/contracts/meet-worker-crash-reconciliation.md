# Packaged Worker crash and original-deadline reconciliation

## Source audit

At `550784049`, the private two-container gate tests graceful parent cancellation
and operator revocation, but not abrupt loss of an installed Worker. Independent
process tests prove the durable dispatch replay fence; they are not evidence
that a real departed browser disappears from the receiver. The Hub already
has a SQL-backed original-deadline reconciler, independently of Worker finish
callbacks. A killed Worker cannot honestly supply a terminal observation.

Add an explicit opt-in crash case to the existing two-Worker gate. Kill only
the freshly created first fixture container after both remote screens move;
validate its exact owned container ID, image and private network beforehand.
Require receiver departure and continuing motion from the surviving Worker,
then normal independent revocation of that survivor. Do not restart the killed
container, replay its assignment, invent a terminal report or shorten its
persisted deadline. Let a fresh native Hub reconciler settle the orphan after
its original deadline using real time and SQL CAS, exactly once. Retain the
survivor's real signed terminal report and assert the crashed report is missing.

SRP: crash injection/owned-target checks and reconciliation observations belong
to a separate test helper; the production dispatcher, media sources, policy
and deadline implementation retain their responsibilities. No shared fixture
expectation is weakened for the existing graceful cases. Tests remain bounded
and headless, with private synthetic policy. This is not automatic room rejoin,
durable full-Hub-process restart, public infrastructure or production evidence.

## Verified private crash slice

The installed-image case passed in **159.27 s** with immutable Worker
`sha256:db7d7af1bf7d2ad20e74f58ba22291ae532ad0f04185dadc0b58a3b271a6c20d`.
Both role-assigned remote screens moved before injection. The exact owned first
container exited with SIGKILL/137; its participant disappeared and the other
screen continued moving. The survivor then obeyed independent operator-policy
revocation and supplied its actual signed terminal observation. The killed
Worker supplied none: its observation remains explicitly missing.

A newly constructed native Hub deadline coordinator settled the orphan only
after the original real-clock deadline, with one `original_deadline_expired`
history event, unchanged assignment and no dispatch replay. A second new
coordinator left that terminal snapshot unchanged. Twelve initial helper and
control-recovery fixture checks passed in **14.32 s**. The real gate includes
no application-source mounts or substituted clocks. It deliberately spends
the remaining original lease budget instead of manufacturing an early finish.
This closes the abrupt container-loss/reconciliation test slice, not automatic
replacement, full Hub restart, GPU/audio crash isolation or all MAP-11 criteria.
