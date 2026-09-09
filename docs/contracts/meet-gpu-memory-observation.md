# Bounded local GPU memory observations (MAP-30)

Existing private GPU preflight already requires at least4096MiB free on GPU0,
and the owned Ollama provider must report the exact pinned Qwen model resident
in VRAM. Previously those checks discarded the measured values. The fixture
now retains the preflight free MiB and post-preload model VRAM bytes and records
them in component and GPU browser JUnit properties.

These are two differently scoped samples, not a continuous peak, a total
Piper/NVENC allocation, exclusive host ownership or GPU reservation. Unavailable
test-double values remain null. A failed preload clears prior residency;
duplicate/extra model rows and invalid residency cannot become successful
measurements. No host process list, model contents, credentials or environment
dump is emitted. Existing commands, five-/45-second deadlines, model profile,
container resources, no-foreign-job rule and Hub admission remain unchanged.

This is a test-infrastructure observation only (SRP), using already queried
provider data rather than adding runtime policy or a second orchestration path.
Deterministic fixture checks and a newly pre-reserved real component run are
required before reporting new actual measurements. Historical runs stay as
recorded; these fields do not retroactively add numeric VRAM evidence to them.

The54 deterministic capacity/provider fixture tests passed in35.44 seconds.
They include missing/duplicate/extra models, malformed row/list shapes, wrong
name/digest, zero/negative/boolean/string/oversized residency, unchanged request
budgets, stale-sample clearing and copied report values. Actual measured numbers
still require the separate real GPU run.

The new pre-reserved component run at Ananta `fd6aa8fec` / Meet `21cff89`
passed in90.67 seconds with unchanged selected inputs and immutable Worker
`6a2ac86f9209`. GPU0 had9864MiB free before setup; the owned provider reported
1,105,419,304 model-resident VRAM bytes after the34.63-second cold preload.
Actual Qwen produced13 output tokens, Piper-CUDA65792 non-silent PCM samples
and NVENC63412 encoded video bytes. Only owned containers were cleaned up.
The Registry accepted this as synthetic-policy TEST evidence and explicitly
denied production-release eligibility. No simultaneous browser delivery,
continuous VRAM peak or exclusive GPU ownership is claimed by this component
run; those are distinct observations.

## Later hardware availability failure, 2026-09-09

At 14:37:53 Europe/Berlin, this Linux host's kernel recorded NVIDIA Xid 79,
`GPU has fallen off the bus`, followed by Xid 154 with recovery action
`Node Reboot Required`. Subsequent numeric `nvidia-smi` queries returned exit 6
and no devices. The retained PCI information names the RTX 3080; that does not
make it usable. A passive 90-second clock observation showed no large wall
steps during that later sample, not a diagnosis of the GPU crash.

No host reboot, GPU reset, driver unload or interruption of unrelated services
was performed. New GPU references cannot run until hardware access is restored;
historical successful runs above remain revision-scoped observations. CPU-only
private tests can continue. The kernel messages identify loss and the requested
recovery action, not whether hardware, power, a driver or a workload caused it.
