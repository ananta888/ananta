# CodeCompass Worker File Context Handoff

## Current Flow

The hub remains the control plane. Workers do not request work from each other.

```text
SnakeChat question
  -> Hub /worker-context
  -> CodeCompassCandidateResolver
  -> ContextFileReaderService
  -> WorkerContextHandoffV3
  -> /snake/ask or worker propose path
```

## Components

| Component | Responsibility |
|-----------|----------------|
| `worker/retrieval/codecompass_output_reader.py` | Load CodeCompass JSONL outputs and attach provenance |
| `worker/retrieval/codecompass_candidate_resolver.py` | Rank file candidates from metadata only |
| `agent/services/context_file_reader_service.py` | Policy-checked file reads inside workspace boundaries |
| `agent/services/worker_context_request_service.py` | Fulfill later read-only context requests through the hub |
| `agent/services/worker_context_handoff_diagnostics_service.py` | Produce non-secret handoff diagnostics |
| `agent/services/worker_contract_service.py` | Build `worker_context_handoff.v3` payloads |
| `agent/routes/snakes.py` | Expose `/worker-context` and `/snake/ask` |

## Handoff Payload

`worker_context_handoff.v3` includes:

- `question`
- `candidate_files`
- `context_files`
- `required_reads`
- `worker_context_requests`
- `manifest_hash`
- `policy_version`
- additive `diagnostics`

Candidate files come from CodeCompass metadata. Context files are original
workspace files read by the hub after policy checks.

## Security Rules

- File reads stay within `workspace_root`.
- Path traversal, symlink escape and workspace-prefix sibling escape are blocked.
- Secret-like files such as `.env`, keys, tokens and credentials are denied.
- Unsupported worker context request actions are rejected.
- The hub owns request fulfillment; workers do not directly orchestrate other
  workers or bypass hub policy.

## v3 First, Compatible Fallback

Callers should try `/worker-context` first when CodeCompass outputs are
available. If no candidates are found or the endpoint is unavailable, existing
`/snake/ask` v1/v2 payloads remain valid. This keeps legacy chat behavior while
allowing richer worker context when the repository has CodeCompass artifacts.

## Diagnostics

Diagnostics include counts and metadata only:

- candidate count
- context file count
- required reads and missing required reads
- total context bytes
- source output kinds
- policy version
- manifest hash

Diagnostics must not include raw file content or secrets.

## Migration Notes

- Existing worker-v2 payloads remain supported.
- New workers can consume `context_files` directly and use
  `worker_context_requests` for later hub-mediated reads.
- OpenCode and `ananta-worker` integrations should treat the handoff payload as
  context input, not as an orchestration command.
