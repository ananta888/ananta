# On-Device LLM Runtime

Local LLM inference on Android devices using llama.cpp, integrated with the
ananta hub–worker architecture.

## Architecture

```
┌─────────────────────────────────────────────┐
│  Android Device (aarch64)                   │
│                                             │
│  ┌─────────────────┐   ┌────────────────┐  │
│  │  llama-server    │   │  ananta-worker │  │
│  │  (OpenAI API)    │◄──│  (Flask app)   │  │
│  │  :8081           │   │  :15080        │  │
│  └─────────────────┘   └────────────────┘  │
│         ▲                                   │
│         │                                   │
│  ┌──────┴──────────┐                        │
│  │  opencode CLI   │                        │
│  │  LOCAL_ENDPOINT │                        │
│  └─────────────────┘                        │
└─────────────────────────────────────────────┘
```

**llama-server** runs as a standalone OpenAI-compatible API server. Both the
ananta-worker and opencode connect to it via standard HTTP endpoints.

This keeps each component independently testable and follows the hub–worker
separation of responsibilities.

## Quick Start

```bash
# 1. Install runtime (downloads llama.cpp, model, opencode)
./scripts/setup-llm-runtime.sh

# 2. Start the LLM server
./scripts/setup-llm-runtime.sh --start-server &

# 3. Verify
curl http://127.0.0.1:8081/health
curl http://127.0.0.1:8081/v1/models

# 4. Test with opencode
LOCAL_ENDPOINT=http://127.0.0.1:8081/v1 ~/.ananta/llm-runtime/opencode/opencode

# 5. Test with worker
LMSTUDIO_URL=http://127.0.0.1:8081/v1 \
DEFAULT_PROVIDER=lmstudio \
DEFAULT_MODEL=smollm2-135m-q8.gguf \
python3 -m agent.ai_agent
```

## Components

### llama-server

Pre-built ARM64 Linux binary from
[llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases).

**Version:** b8994  
**Binary:** `$ANANTA_LLM_HOME/llama-cpp/llama-server`  
**Shared libs:** Located alongside the binary (`libggml*.so`, `libllama*.so`)

Key flags:
- `-c 16384` — context window (tokens)
- `-np 1` — single parallel slot (uses full context)
- `--override-kv llama.context_length=int:16384` — extend beyond model default
- `--host 127.0.0.1 --port 8081` — listen address

### GGUF Model

**Default model:** SmolLM2-135M-Instruct Q8_0 (~139MB)  
**Location:** `$ANANTA_LLM_HOME/models/smollm2-135m-q8.gguf`

Performance on Fairphone 6 (aarch64):
- Prompt processing: ~140 tokens/second
- Text generation: ~16 tokens/second

### opencode

Terminal-based AI coding assistant.
[opencode-ai/opencode](https://github.com/opencode-ai/opencode)

**Version:** v0.0.55  
**Binary:** `$ANANTA_LLM_HOME/opencode/opencode`

Configuration via `.opencode.json` in the project root:

```json
{
  "agents": {
    "coder": { "model": "local.<model-id>", "maxTokens": 200 },
    "task":  { "model": "local.<model-id>", "maxTokens": 200 },
    "title": { "model": "local.<model-id>", "maxTokens": 40 }
  }
}
```

The `<model-id>` must match the model ID from `/v1/models`. With the default
model, this is `smollm2-135m-q8.gguf`.

**Environment variable:** `LOCAL_ENDPOINT=http://127.0.0.1:8081/v1`

### Worker Integration

The worker's `OpenAICompatibleModelProvider` connects to any OpenAI-compatible
endpoint. Configure via environment variables:

```bash
LMSTUDIO_URL=http://127.0.0.1:8081/v1
DEFAULT_PROVIDER=lmstudio
DEFAULT_MODEL=smollm2-135m-q8.gguf
```

The provider is defined in `worker/core/model_provider.py`.

## Network Topology

`127.0.0.1` means different things depending on context:

| Context | 127.0.0.1 refers to | How to reach host llama-server |
|---------|---------------------|-------------------------------|
| Host shell (Termux) | Host itself | `127.0.0.1:8081` |
| proot Ubuntu | Host (shared network namespace) | `127.0.0.1:8081` |
| Android app process | Host device | `127.0.0.1:8081` |
| Android emulator | Emulator itself | `10.0.2.2:8081` or `adb reverse tcp:8081 tcp:8081` |

For device testing via ADB, port forwarding may be needed:

```bash
adb reverse tcp:8081 tcp:8081
```

## Known Limitations

### Context Window

SmolLM2-135M has a native context of 8192 tokens. opencode sends ~13k tokens of
system prompt + tool definitions. To work around this:

- `--override-kv llama.context_length=int:16384` extends the context via RoPE
- Quality degrades beyond the model's training context
- For production use, prefer models with 32k+ native context (e.g. Qwen2.5-3B,
  Llama 3.2-3B)

### Model Quality

SmolLM2-135M (135M parameters) is a test/demo model. Responses are often
incoherent for complex prompts. Use a larger model for real coding tasks:

| Model | Size | Context | Notes |
|-------|------|---------|-------|
| SmolLM2-135M Q8_0 | 139MB | 8k | Demo only |
| Qwen2.5-Coder-3B Q4_K_M | ~2GB | 32k | Good for coding |
| Llama 3.2-3B Q4_K_M | ~2GB | 128k | General purpose |
| Qwen2.5-Coder-7B Q4_K_S | ~4GB | 32k | Best quality |

### Worker Timeout

`OpenAICompatibleModelProvider` defaults to 60s timeout. For local models with
large prompts (13k+ tokens), increase via constructor parameter or environment
variable to at least 120s.

## Building on aarch64 Host

The Android SDK tools are x86-64 binaries. On aarch64:

```bash
# Set up qemu wrappers for aapt2, cmake, ninja
./scripts/setup-android-build-env.sh

# Build APK (without native llama — stub only)
cd frontend-angular/android
./gradlew :app:assembleDebug

# Build APK with native llama stub
./gradlew :app:assembleDebug -PanantaEnableLlamaCppRuntime=true
```

See `scripts/setup-android-build-env.sh` for details.

## File Locations

```
$ANANTA_LLM_HOME/                    # default: ~/.ananta/llm-runtime/
├── llama-cpp/                       # llama.cpp binaries + shared libs
│   ├── llama-server
│   ├── llama-cli
│   ├── libggml*.so
│   └── libllama*.so
├── models/                          # GGUF model files
│   └── smollm2-135m-q8.gguf
├── opencode/                        # opencode CLI
│   └── opencode
└── downloads/                       # cached tarballs
    ├── llama-b8994-bin-ubuntu-arm64.tar.gz
    └── opencode-linux-arm64.tar.gz
```
