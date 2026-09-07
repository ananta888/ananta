# Combined spoken dialog GPU gate — implementation plan

MAP-22 follow-up after the current Hub/Worker/Meet dialog passed with synthetic
model/WAV output. This plan is not a completed GPU or production release claim.

## Isolation decision

Use test-owned GPU inference containers on a new internal-only Docker network.
Do not execute new code in the serving worker, restart a public service, mutate
its networks, read its signing key or reuse writable model/state directories.
The existing local immutable image identities and read-only model mounts may be
resolved with narrow Docker inspections. Images must already exist locally;
there is no download, CPU/cloud fallback or external network access.

Start a separate Ollama provider from the existing pinned local image with its
existing model volume mounted read-only, a bounded temporary writable home/state
and only the owned internal network. Start the current-source media worker with
read-only `worker/meet_media` and `ananta_contracts`, read-only pinned Piper models
and the existing allowlisted driver projection. Both retain bounded memory,
process counts, lifetime and cleanup. The Worker alone executes the exact
Hub-issued child media assignment; the Hub remains the only task owner.

Use an ephemeral test-only signing key and replay store for the private media
HTTP endpoint. No production credentials, host Docker socket, browser profile,
user capture or arbitrary tools enter either container. Only validated owned
UUID names are removed on teardown, including partial/uncertain setup failures.

## Incremental checks

1. Deterministic fixture tests for exact image/model/driver/network bindings,
   closed configuration, timeouts and unconditional owned cleanup.
2. A real private HTTP turn through the current Worker, actual local Qwen GPU
   output, pinned Piper CUDA speech and existing NVENC renderer. Check exact
   task/lease, profile, samples, token usage and bounded response, without logging
   text, PCM, MP4 or credentials. This remains a technical component probe.
3. Reuse the real Hub/chat admission + browser fixture with this private Worker.
   Correlate the actual model answer instead of requiring a fixed synthetic
   string; check local source completion and actual non-silent remote decoding.
4. Verify independent speech pause and Hub stop/cancellation; retain synthetic
   deterministic revocation races. Do not equate local played samples with exact
   remote delivery or infer public TURN, production trust or release evidence.

All gates are opt-in, bounded and fully headless. A missing GPU/image/model or
failed gate is reported as such, never replaced with synthetic success. The
broader voice-asset, live revocation, multi-node and soak TODOs remain open.
