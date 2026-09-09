# Hub-reserved Meet reference tests

`python -m scripts.run_meet_test_gate --output-directory NEW_DIRECTORY` runs
one fixed, headless two-packaged-Worker timing/resource/reconnect reference.
The optional closed `--profile` selector also supports `gpu-components`,
`gpu-avatar` and `gpu-voices`. GPU profiles require an explicit immutable
`MEET_DIALOG_GPU_PACKAGED_IMAGE`; absence or a mutable tag fails before
reservation, never falling back to the serving Worker. Component inference
has a 240-second outer deadline; the browser/GPU profiles have 360 seconds.
All profiles force the short-test soak setting to zero instead of inheriting
an unrelated long-run opt-in. Selection, original deadlines and environment
are bound into the reserved execution profile; arbitrary test nodes, command
arguments and timeout extensions are not accepted.
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

GPU component execution verifies actual Qwen/Piper-CUDA/NVENC bytes, but has no
browser receiver. GPU browser profiles use a host-side dialog executor, a
separate packaged inference Worker and an isolated browser container. They
exercise actual Qwen/Piper speech plus independent owned screen/neutral avatar,
not two packaged dialog publishers or live NVENC avatar delivery. The fixture
now explicitly passes the selected timing mode into its manually assembled Hub
service and records that negotiation; setting an unused environment variable
alone would not verify the new clock path. Resource/occupancy assertions remain
specific to the two-Worker profile. Profile success does not stand in for the
remaining measured GPU, public trust or long-soak acceptance criteria.

The initial default reference passed at Ananta `10d655c6c` / Meet `28eff78`
with immutable Worker `6a2ac86f9209` in 65.51 s, one test and zero failures,
errors or skips. Source/bundle inputs were unchanged; the Hub Registry accepted
the pre-reserved test result and explicitly rejected production eligibility.
Both Workers had one active dialog during the sample and zero after cleanup.
Observed active memory was 369,598,464 / 344,862,720 bytes under each 1-GiB
quota; sampled PID counts were 104 / 102 and returned to five. These are sparse
startup/active/terminal observations, not continuous CPU/RAM peak guarantees.

The first pre-reserved `gpu-components` reference passed at Ananta `731d588e5`
and Meet `28eff78` with the same immutable Worker, in 96.671 seconds including
test setup/cleanup (one pass, zero skips/errors, unchanged inputs). Actual
model preload took 34.59 seconds; Qwen returned thirteen output tokens, Piper
produced 65,792 non-silent PCM samples and NVENC produced 61,558 video bytes.
All private inference containers were removed by fixture cleanup. This is
component acceptance, not browser delivery, exclusive host-GPU occupancy or a
public deployment result. Foreign GPU jobs observed before/after owned gates
were never terminated or reconfigured.
