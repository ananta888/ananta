# Deterministic Source-Grounded Answers

Ananta enforces source-grounding in the hub control plane. The LLM can formulate text, but it does not define truth or source identity.

## Rules

- The model may cite only Hub-registered source IDs (`SRC_*`, `RUN_*`).
- The Hub may issue IDs automatically; no human input is required.
- Workers and models never mint or self-assign IDs.
- Missing citations for factual claims are treated as unverified/failed.
- Tool-result claims require tool evidence (`RUN_*`, `test_result`, or `generated_artifact`).
- Cloud scope restrictions apply to citations (`allowed_for_llm_scope=false` sources are rejected).
- No post-hoc or heuristic source invention.

## Source Identity

- `SRC_*`: immutable, admitted retrieval/source identities (repo files, RAG
  chunks, artifacts, wiki chunks or approved datasets).
- `RUN_*`: Hub-reserved execution identities, issued before execution and
  bound to task, assignment, lease, revision, sources and environment.

The generic Hub registry stores only bounded identity and digest metadata. An
automatically admitted source is binding-addressed. An externally supplied ID
must name its external issuer and pass the same immutable registration; the
string alone provides no authority.

Test and synthetic records carry an explicit evidence scope. They can exercise
the complete automatic flow but cannot satisfy a production release gate.

The catalog is anchored by:
- `retrieval_trace_id`
- `retrieval_context_hash`
- `retrieval_manifest_hash`

## Valid grounded_answer.v1 Example

```json
{
  "schema": "grounded_answer.v1",
  "answer": "The toy miner uses double_sha256 and found a valid nonce under an artificial target.",
  "claims": [
    {
      "claim_id": "CLM_0001",
      "text": "The algorithm uses double_sha256 over a simplified header.",
      "claim_type": "source_fact",
      "citation_refs": ["SRC_0001"],
      "confidence": "verified"
    },
    {
      "claim_id": "CLM_0002",
      "text": "The concrete nonce/hash comes from a recorded tool run.",
      "claim_type": "tool_result",
      "citation_refs": ["RUN_0001"],
      "confidence": "verified"
    }
  ],
  "unsupported_notes": []
}
```

## Invalid Hallucinated Example

```json
{
  "schema": "grounded_answer.v1",
  "answer": "Hash verified.",
  "claims": [
    {
      "claim_id": "CLM_0999",
      "text": "Nonce 42 and hash abc... were computed.",
      "claim_type": "tool_result",
      "citation_refs": ["SRC_9999"],
      "confidence": "verified"
    }
  ],
  "unsupported_notes": []
}
```

This is rejected (`failed_unknown_source`) because `SRC_9999` is not registered
and bound to the assignment.

## Minimal Deterministic Example

See `tests/fixtures/bitcoin_mining_demo/` and
`scripts/run_bitcoin_mining_citation_evidence.py` for a deterministic toy flow.
Its generated run reference is explicitly test-scoped and is not production
release evidence.
