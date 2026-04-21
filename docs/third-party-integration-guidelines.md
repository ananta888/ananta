# Third-Party Integration Guidelines

Third-party tools, providers and worker adapters must preserve Ananta's hub-worker control model.

## Guideline Version

- Version: `v1`
- Catalog builder: `agent.integration_guidelines.build_integration_guidelines`

## Minimum Requirements

| ID | Requirement |
| --- | --- |
| `contract_first` | Declare tool, worker or provider contract compatibility before exposure. |
| `least_privilege` | Request only needed scopes, filesystem access and network access. |
| `auditability` | Emit audit or product events for mutating or externally visible operations. |
| `fail_closed` | Unknown capabilities, unsupported operations and ambiguous responses fail closed. |
| `hub_boundary` | Do not create worker-to-worker orchestration or independent task queues. |
| `test_evidence` | Provide contract, security, error-path and representative happy-path tests. |

## Review Rule

Do not expose a third-party adapter until every minimum requirement has explicit evidence.

## Adapter Checklist

- State the contract version and supported operations.
- Define auth, secrets and least-privilege boundaries.
- Define audit action names and trace/context fields.
- Define health/preflight behavior and failure modes.
- Add tests for unsupported operations and malformed provider responses.
- Confirm that the hub remains the task queue owner.
