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
