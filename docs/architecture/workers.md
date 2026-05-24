# Worker LLM Endpoint Guidance

## Preferred Path
When policy enforcement is required, workers and agents should call:

`Agent/Worker -> Ananta LLM Interceptor -> Upstream Provider`

Direct provider access bypasses Ananta policy, context-gating, and redaction controls.

## Migration
Replace direct provider base URLs with the interceptor base URL:
- from: direct OpenAI/OpenRouter/LM Studio endpoint
- to: `http://127.0.0.1:8787/v1` (or deployed interceptor URL)

Use stable model aliases such as:
- `intercepted-coder`
- `ananta-interceptor/intercepted-coder`

## Security Warning
Direct provider calls are allowed only for explicitly approved exceptions.
Default operational guidance is interceptor-first to keep trust boundaries auditable.

