# Local Meet audio recognition

This is an execution-only, bounded worker adapter, not an enabled Meet receive
integration. Sending media does not authorize receiving it. The MDS receive
transport and a production `ReceiveLeasePort` must supply and continually verify
the exact Hub assignment and current Meet publication permission before use.
Until those adapters exist, only explicitly synthetic local probes are available.

## Boundaries

`MeetAudioReceiver` accepts one utterance window under a tenant/project, task,
dispatch lease, runtime, session generation, room membership, peer, publication
and publication epoch binding. It rejects the worker's own publication source.
Only separately authorized microphone or screen audio is supported; camera/video
understanding is not implemented here. Audio must actually be resampled to mono
16 kHz PCM16LE by the upstream adapter, not merely labelled as that format.

Packets are 10–100 ms with contiguous sample offsets. The default maximum is
10 seconds / 320,000 bytes and a 30-second wall deadline. Duplicate, reordered,
cross-scope and over-budget input closes the stream. Input and finalization are
serialized; cancellation signals the ASR child before waiting for stream locks.
The existing Voice streaming implementation owns bounded buffering and cleanup.
No files, transcripts or meeting audio are persisted by this receiver.

`MeetAsrPipeline` uses the existing bounded subprocess supervisor. The child has
at most 20 seconds (or the remaining assignment deadline), bounded output and
periodic authorization/cancellation checks. It receives only WAV bytes and an
explicit `de`/`en` language, not provider credentials, paths, conversation history
or personalization context. Only operator-selected GPU library paths augment its
minimal environment. Cancellation terminates the child process group. The child
uses the existing safe audio decoder and FasterWhisper backend, with CUDA float16,
beam size 1 and local VAD. CPU/cloud fallback and runtime downloads are disabled.

The structured result includes exact source binding and sample range. It does
not authorize publication, retention, training or further provider use. A future
Hub task adapter must separately admit each of those operations. Continuous
renewable receive and MDS transport wiring remain open, as does visual analysis.

## Explicit setup and synthetic GPU probe

The separate worker image pins `faster-whisper==1.2.1` and
`ctranslate2==4.6.0`. The model is
[`Systran/faster-whisper-small`](https://huggingface.co/Systran/faster-whisper-small/tree/536b0662742c02347bc0e980a01041f333bce120),
revision `536b0662742c02347bc0e980a01041f333bce120`. Exact file sizes and SHA-256
digests are checked during explicit provisioning and again before inference.
Provisioning never replaces an existing model; invalid or incomplete content
fails closed and needs operator diagnosis. There is no automatic policy change.
The [upstream implementation](https://github.com/SYSTRAN/faster-whisper) documents
the CUDA 12/cuDNN 9 prerequisites and its MIT license.

```sh
.venv/bin/python scripts/setup_meet_asr.py /absolute/private/meet-media-state
```

Use an existing private state directory (mode 0700) and mount its `models` directory
read-only into the dedicated worker at `/models`. Follow the existing local media
worker deployment runbook for GPU access; do not restart public Hub/Meet services
or activate broad receive grants merely to run this probe.

```sh
docker exec ananta-meet-media-meet-media-worker-1 \
  timeout 90 python -m worker.meet_media.asr_smoke
```

The probe generates a fixed German sentence with local Piper, decodes it to
canonical PCM, sends packets through the receiver and checks local GPU ASR.
Its lease is explicitly synthetic. Output contains counts and classification,
not the transcript. Success is a local technical observation, **not** a live
Meet receive test, a Hub-registered run or production release evidence.

## Structure and tests

The receiver depends on a narrow authorization port; it cannot issue/renew its
own lease (DIP/ISP). Model admission, buffering, ASR execution and subprocess
supervision remain separate responsibilities (SRP). Existing Voice components
are reused rather than copied. The package's `create_app` facade is now lazy,
preserving its public call while removing the accidental dependency from a
worker backend import to the Flask application. The subprocess runner's GPU
library-path option is additive; its default environment is unchanged.

Headless coverage lives in `tests/test_meet_audio_receive.py` and
`tests/test_meet_asr_provisioning.py`, with existing Voice audio-security,
streaming and backend regressions. Synthetic model bytes are generated inside
tests; no live recordings, credentials or model binaries are committed.
