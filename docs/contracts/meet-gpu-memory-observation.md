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
