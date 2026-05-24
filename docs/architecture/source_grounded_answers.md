# Deterministic Source-Grounded Answers

Ananta enforces source-grounding in the hub control plane. The LLM can formulate text, but it does not define truth or source identity.

## Rules

- The model may cite only provided source IDs (`SRC_*`, `RUN_*`).
- Missing citations for factual claims are treated as unverified/failed.
- Tool-result claims require tool evidence (`RUN_*`, `test_result`, or `generated_artifact`).
- Cloud scope restrictions apply to citations (`allowed_for_llm_scope=false` sources are rejected).
- No heuristic source invention by default.

## Source Identity

- `SRC_*`: retrieval/source catalog entries (repo files, rag chunks, artifacts, wiki chunks).
- `RUN_*`: deterministic tool-run evidence entries with hashes (stdout/stderr/result payload).

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

This is rejected (`failed_unknown_source`) because `SRC_9999` is not in the catalog.

## Minimal Deterministic Example

See `tests/fixtures/bitcoin_mining_demo/` and `scripts/run_bitcoin_mining_citation_evidence.py` for a deterministic toy flow.
