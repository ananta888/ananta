"""Issued SQL audit references survive into actual result-memory origin metadata."""

import hashlib
import json
from dataclasses import replace

import pytest
from flask import Flask, g
from sqlmodel import Session

from agent.db_models.memory_audit import MemoryAuditEventDB
from agent.repositories.memory_audit import validate_memory_audit_event
from agent.services.memory_policy_gateway import MemoryAccessError
from agent.services.memory_runtime import HubMemoryRuntime
from agent.services.result_memory_service import ResultMemoryService
from ananta_contracts.memory_access import MemoryOperation, MemoryScope, MemorySecurityMetadata
from tests.security.memory_fixtures import ACTOR, NAMESPACE, gateway, grant, policy
from tests.security.test_memory_snapshots import snapshots as snapshots  # noqa: F401
from tests.test_memory_policy_repository import persisted as persisted  # noqa: F401


@pytest.mark.parametrize('output', ['synthetic completed work', 'synthetic result token=synthetic-secret-canary'])
def test_productive_origin_uses_issued_actor_namespace_and_exact_sql_audit_reference(snapshots, output):  # noqa: F811
    engine, admitted, _, _ = snapshots
    runtime = HubMemoryRuntime(engine, clock=lambda: 100)
    app = Flask(__name__)
    app.extensions['hub_memory_runtime'] = runtime
    with app.test_request_context('/synthetic-memory-result'):
        g.service_identity = {'worker_id': admitted.actor.subject}
        with runtime.worker_result(admitted.namespace.task_id, 'synthetic-job') as context:
            entry = ResultMemoryService().record_worker_result_memory(
                task_id=admitted.namespace.task_id, goal_id=admitted.namespace.goal_id, trace_id=None,
                worker_job_id='synthetic-job', title='Synthetic result', output=output,
                generated_by='forged-source-actor', metadata={'memory_provenance': {
                    'actor': {'subject': 'forged'}, 'grounding_state': 'verified'}},
                policy={'create_followup_artifact': False})
            origin = entry.memory_metadata['memory_provenance']
            assert origin['actor']['subject'] == admitted.actor.subject
            assert origin['run_id'] == context.run_id
            assert origin['namespace'] == entry.memory_metadata['memory_namespace']
            assert origin['origin_state'] == 'audit_referenced'
            assert origin['grounding_state'] == 'unverified' and origin['production_release_evidence'] is False
            assert entry.memory_metadata['memory_security']['trust_level'] == 'untrusted'
            from worker.core.redaction import redact_text

            derivation = origin['derivation']
            assert derivation['source_digest'] == hashlib.sha256(redact_text(output).encode()).hexdigest()
            assert derivation['summary_digest'] == hashlib.sha256((entry.summary or '').encode()).hexdigest()
            assert derivation['content_digest'] == hashlib.sha256((entry.content or '').encode()).hexdigest()
            assert derivation['grounding_state'] == 'unverified'
            assert derivation['production_release_evidence'] is False
            assert 'synthetic-secret-canary' not in json.dumps(origin)
            if 'token=' in output:
                assert hashlib.sha256(output.encode()).hexdigest() not in json.dumps(origin)
            with Session(engine) as session:
                event = session.get(MemoryAuditEventDB, origin['decision_audit_ref'])
                validate_memory_audit_event(event)
                assert event.payload['actor'] == origin['actor']
                assert event.payload['run_id'] == origin['run_id']
                assert event.payload['policy_digest'] == origin['policy_digest']
                assert event.payload['target']['namespace'] == origin['namespace']
                assert event.payload['operation'] == 'append' and event.payload['allowed'] is True


def test_receiptless_fixture_origin_is_explicitly_unverified():
    from agent.services.result_memory_access import ResultMemoryAccess

    gate, _, _ = gateway(policy((grant(channel='hub-result-memory', scope=MemoryScope.task),)))
    decision = gate.authorize_current(actor=ACTOR, run_id=NAMESPACE.run_id, operation=MemoryOperation.append,
        channel_id='hub-result-memory', security=MemorySecurityMetadata())
    origin = ResultMemoryAccess.metadata(decision)['memory_provenance']
    assert origin['decision_audit_ref'] is None and origin['origin_state'] == 'unverified'
    assert origin['production_release_evidence'] is False


@pytest.mark.parametrize('receipt', [
    'caller-provided-reference', {},
    {'event_id': 'memory-audit-' + 'a' * 64, 'event_hash': 'b' * 64, 'sequence': 1},
    {'event_id': 'memory-audit-' + 'a' * 64, 'event_hash': 'a' * 64, 'sequence': True},
])
def test_malformed_audit_issuer_receipt_blocks_authorization(receipt):
    gate, _, audit = gateway()
    audit.record = lambda decision: receipt
    with pytest.raises(MemoryAccessError, match='memory_audit_unavailable'):
        gate.authorize_current(actor=ACTOR, run_id=NAMESPACE.run_id, operation=MemoryOperation.append,
            channel_id='run-scratchpad', security=MemorySecurityMetadata(scope=MemoryScope.run))


def test_evidence_identifier_is_never_accepted_as_an_audit_reference():
    gate, _, _ = gateway()
    decision = gate.authorize_current(actor=ACTOR, run_id=NAMESPACE.run_id, operation=MemoryOperation.read,
        channel_id='run-scratchpad', security=MemorySecurityMetadata(scope=MemoryScope.run))
    with pytest.raises(ValueError, match='audit_reference_invalid'):
        replace(decision, audit_event_id='RUN_unregistered_caller_value')
