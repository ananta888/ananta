# LLM Interceptor Architecture

## Boundary
Exact service boundary:

`agent/client -> Ananta LLM Interceptor -> upstream provider`

The interceptor is a real HTTP service exposing OpenAI-compatible endpoints. It is not a prompt-only technique.

## Goals
- Provide an OpenAI-compatible entrypoint for agents.
- Keep provider credentials server-side only.
- Enforce deterministic policy, redaction, and routing controls before upstream calls.

## Endpoint Surface
MVP:
- `POST /v1/chat/completions` required
- `GET /v1/models` optional but recommended for compatibility

Later optional:
- `POST /v1/responses`

Both streaming and non-streaming semantics must be handled explicitly and tested separately.

## Compatibility Rules
- Unsupported request parameters must be handled deterministically:
  - forwarded safely
  - ignored with explicit reason
  - or rejected with stable error code
- The interceptor may adapt formatting and routing metadata, but must preserve user task intent unless policy blocks the request.

## Non-Goals
- No autonomous policy bypass.
- No silent direct provider access from workers/agents.
- No prompt-controlled policy mutation.

