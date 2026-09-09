# Hub-reserved Meet reference tests

`python -m scripts.run_meet_test_gate --output-directory NEW_DIRECTORY` runs
one fixed, headless two-packaged-Worker timing/resource/reconnect reference.
Set the existing explicit immutable `MEET_MULTI_WORKER_IMAGE`,
`MEET_TEST_PROXY_IMAGE`, private `MEET_TEST_PUBLIC_DIR` and local native-tool
environment first. It never pulls/deploys an image, changes trust, provisions a
public room or overrides admission. The profile enables actual cgroup/slot
observations, timing, pooled WAL and the existing two-Worker test. It has a
420-second outer deadline and terminates only its own test process group if
that deadline is exceeded. Standard input is closed; no human gate can unblock
the test. The fixtures retain their independent original deadlines and cleanup.

Before execution the controller verifies clean selected tracked Ananta inputs
and a clean companion checkout, hashes them and the actual private frontend
bundle, and uses the existing Hub Evidence Registry to admit the test harness
and reserve a test-scope run. The Worker receives only the closed assignment
projection. Ananta source admission binds the current AGENTS and Meet quality/
capacity policy documents; companion revision/content and frontend bytes are
pinned in the run's environment binding. No caller supplies a SRC/RUN string.

The Ananta input selection includes application, Worker, contracts, test,
runner, policy and Worker-build sources. Unrelated Compose-next edits and
untracked frontend runtime data are neither overwritten nor included as tested
inputs. Selected dirty/untracked sources block before reservation. Input
changes during execution, missing/invalid JUnit, skips, errors and nonzero
exit codes cannot yield an accepted result. Existing output directories are
not overwritten. Reports/logs/JUnit and the persistent local registry are
runtime data; don't commit raw logs, volatile IDs or a private database.

The report distinguishes a passed test from release eligibility. All identities
here are explicitly synthetic/test-scoped and can never satisfy a production
release gate. Earlier unreserved local runs remain technical observations;
this runner cannot retroactively create evidence identities for them. Source
snapshotting, bounded execution and Registry issuance remain separate
responsibilities. The existing browser evidence adapter gains only an optional
policy-path input; its older callers keep their previous default policy.
