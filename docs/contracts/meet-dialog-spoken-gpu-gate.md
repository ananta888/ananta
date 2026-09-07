# Combined spoken dialog GPU gate — implementation plan

MAP-22 follow-up after the current Hub/Worker/Meet dialog passed with synthetic
model/WAV output. Isolation and the real GPU HTTP component gate are implemented;
the combined Hub/browser gate remains open. No production release is claimed.

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

## Isolated component verification

`tests/meet_dialog_gpu_fixture.py` starts only its UUID-named internal network,
Ollama and current-source media Worker. The pinned Ollama image uses UID 1000
(`ubuntu`), so its small temporary writable directory is precisely
`/home/ubuntu/.ollama`; using `/root` initially made startup fail read-only.
The fix did not make the root filesystem or model store writable. Docker's
read-only `volume-subpath=models` excludes the serving Ollama identity files.
No HOME variable, serving key, provider environment or public port is reused.

31 deterministic new/existing fixture checks passed in 25.68 s, covering
configuration/driver validation, network membership and private addresses,
source/model/key mount boundaries and reverse-order cleanup after partial or
uncertain creation/start/health failure. New fixture/test modules pass Ruff.

`MEET_DIALOG_GPU_GATE=1` with `tests/test_meet_dialog_gpu_turn.py` passed against
the actual local RTX 3080 and current Worker source in 58.28 s. The measurement
inside the test, including setup and cold inference, was 49.1 s: 108 input and
13 output tokens, 65,792 real non-silent PCM samples, and a 63,868-byte NVENC MP4.
The result traversed the actual signed private Worker HTTP adapter and pinned
voice/sample validation. The local Qwen adapter checked the configured model
digest and nonzero VRAM residency; Piper and NVENC retained their fail-closed
GPU requirements. Both containers and the network were absent after cleanup.

The ephemeral report is `/tmp/ananta-meet-dialog-gpu-component.xml`; it contains
only counts and classification, no generated text/audio/video or signing key.
This was a synthetic component task, not a Hub-issued production run or a Meet
receiver test. Cold startup/inference timing must not be presented as warm
dialog latency. The 25-second spoken callback budget still needs validation
with actual inference in the combined gate; it has not been silently widened.
