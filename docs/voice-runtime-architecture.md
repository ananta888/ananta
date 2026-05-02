# Voice Runtime Architecture (Option C)

## Decision

Ananta uses **Option C: dedicated `voice-runtime` service**.

The hub remains the control plane (policy, approvals, routing, audit).  
`voice-runtime` is execution-only and handles audio processing behind a stable API.

## Why this split

- Clients stay thin and backend-agnostic.
- Voice backend swaps/fallbacks (Voxtral, whisper.cpp, faster-whisper, others) are isolated in one service.
- Privacy and policy controls stay centralized in hub routes.
- No worker-to-worker orchestration is introduced.

## Boundary

- **Hub public API**: `/v1/voice/transcribe`, `/v1/voice/command`, `/v1/voice/goal`, `/v1/voice/capabilities`
- **Voice runtime internal API**: `/health`, `/v1/models`, `/v1/audio/transcriptions`, `/v1/audio/chat`

Clients must not couple directly to runtime-specific model semantics.
