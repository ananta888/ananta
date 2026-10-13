"""Authoritative memory-access origin; never a source/production evidence grant."""

import hashlib
import json
from dataclasses import asdict

from agent.services.memory_policy_gateway import MemoryPolicyDecision


def memory_access_origin(decision: MemoryPolicyDecision) -> dict:
    if not isinstance(decision, MemoryPolicyDecision) or not decision.allowed or decision.target is None:
        raise ValueError('memory_origin_authorization_required')
    return {
        'schema': 'ananta.memory-access-origin.v1',
        'actor': asdict(decision.actor), 'run_id': decision.run_id, 'trace_id': decision.trace_id,
        'channel_id': decision.target.channel_id, 'namespace': asdict(decision.target.namespace),
        'operation': decision.operation.value, 'policy_revision': decision.policy_revision,
        'policy_digest': decision.policy_digest, 'decision_audit_ref': decision.audit_event_id,
        'origin_state': 'audit_referenced' if decision.audit_event_id else 'unverified',
        'grounding_state': 'unverified', 'production_release_evidence': False,
    }


def result_memory_derivation(*, redacted_output: str, summary: str | None,
                             content: str | None, policy: dict) -> dict:
    """Describe the local deterministic transformation of an unverified result.

    Hash only the admitted, redacted representation. No original secret hash,
    caller-supplied source identity or raw content becomes provenance metadata.
    """
    def digest(text: str | None) -> str:
        return hashlib.sha256((text or '').encode('utf-8')).hexdigest()

    encoded_policy = json.dumps(policy, sort_keys=True, separators=(',', ':'),
                                ensure_ascii=False, allow_nan=False)
    return {
        'schema': 'ananta.result-memory-derivation.v1',
        'source_kind': 'worker_result',
        'source_representation': 'storage_output_after_redaction_policy',
        'source_digest': digest(redacted_output),
        'summary_digest': digest(summary), 'content_digest': digest(content),
        'transform': 'worker_result_compact_v3', 'transform_policy_digest': digest(encoded_policy),
        'grounding_state': 'unverified', 'production_release_evidence': False,
    }


def memory_tree_derivation(*, summary: str | None, chunks: list) -> dict:
    """Bind a stored summary to the exact authorized chunk observations."""
    return {
        'schema': 'ananta.memory-tree-derivation.v1',
        'source_kind': 'memory_tree_chunks',
        'attribution': 'observed_parent_links',
        'summary_digest': hashlib.sha256((summary or '').encode('utf-8')).hexdigest(),
        'parents': [
            {'chunk_id': chunk.id,
             'content_digest': hashlib.sha256((chunk.content or '').encode('utf-8')).hexdigest()}
            for chunk in sorted(chunks, key=lambda chunk: chunk.id)
        ],
        'grounding_state': 'unverified', 'production_release_evidence': False,
    }
