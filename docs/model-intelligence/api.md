# Model Intelligence API and CLI

## REST resources

The public prefix is `/api/model-intelligence`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/capabilities` | Truthful adapter and analyzer capabilities |
| `POST` | `/jobs` | Submit a Hub-owned analysis job |
| `GET` | `/jobs` | List tenant jobs with cursor pagination |
| `GET` | `/jobs/{job_id}` | Read one tenant job |
| `POST` | `/jobs/{job_id}/cancel` | Request cancellation |
| `GET` | `/jobs/{job_id}/report` | Read the canonical report |
| `GET` | `/jobs/{job_id}/graph` | Traverse a bounded model graph |
| `GET` | `/artifacts/{artifact_id}` | Read authorised artifact metadata/content |

Mutation requests use `Idempotency-Key`. Version-sensitive mutations use the
documented ETag. List endpoints cap `page_size` at 100. Every error returns a
stable `reason_code`, a sanitised message and retryability.

Authorization is evaluated before resource metadata is exposed. A resource
owned by another tenant is returned through the same not-found boundary as an
unknown resource.

## CLI

The CLI module is `agent.cli.model_intelligence`. It communicates only with the
public API and never imports Hub domain services.

```bash
python -m agent.cli.model_intelligence \
  --base-url http://127.0.0.1:5000 \
  create \
  --request-json request.json \
  --idempotency-key local-example-1
```

Other commands are:

```text
list
get JOB_ID
cancel JOB_ID
artifact ARTIFACT_ID
report JOB_ID
```

`ANANTA_API_TOKEN` supplies the bearer token. Output is canonical JSON. Stable
exit classes distinguish authentication, not found, conflicts, unavailable
service and other API failures.

## Capability interpretation

Capability states are:

- `supported`: implemented and contract-probe compatible
- `conditional`: available only when the named local dependency or policy is
  satisfied
- `unsupported`: deliberately unavailable with a stable reason code

Remote inference endpoints do not imply remote hidden-state or attention
access. Such requests return an unsupported reason instead of empty trace data.
