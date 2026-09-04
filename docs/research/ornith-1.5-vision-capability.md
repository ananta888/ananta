# Ornith 1.5 vision capability

Vision is not enabled from a model tag. The 9B and 35B GGUF repositories expose
separate pinned `mmproj` artifacts, but this proves only artifact availability.
Each model/runtime pair remains `unknown` until the exact weight digest,
projection digest, runtime digest and generated fixture digest complete a
Hub-bound run.

The benchmark contract is `benchmarks/models/ornith-1.5-vision.v1.json`.
Fixtures are deterministic drawing descriptions under
`tests/fixtures/models/ornith_vision`; a runner renders them locally so no
third-party image license enters the suite. They cover OCR, a hub-worker
diagram, a UI-like screenshot and general object description.

Missing projection yields text-only availability. Corrupt, oversized or
unsupported images fail the vision request without disabling text inference.
Raw images and model reasoning are not persisted by default.
