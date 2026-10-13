"""Closed, immutable BPMN source projection for an already authorized Hub run."""

import hashlib
import re

from agent.services.bpmn_workflow_preflight import workflow_start_plan
from agent.visual_process.definition_snapshot_contract import definition_snapshot_hash


class BpmnDefinitionSnapshotError(ValueError):
    pass


def project_bpmn_definition(binding):
    metadata = binding.request.metadata
    source = metadata.get('bpmn_source_xml')
    if not isinstance(source, str) or not source:
        raise BpmnDefinitionSnapshotError('workflow_bpmn_definition_unavailable')
    try:
        encoded = source.encode('utf-8')
        source_hash = metadata.get('bpmn_source_sha256')
        if (len(encoded) > 512 * 1024 or not isinstance(source_hash, str)
                or re.fullmatch(r'[a-f0-9]{64}', source_hash) is None
                or hashlib.sha256(encoded).hexdigest() != source_hash):
            raise ValueError('source_integrity')
        plan = workflow_start_plan(binding.request, tenant_id=binding.tenant_id)
        # The admitted BPMN definition and public VisualProcess design have
        # distinct hashes when a caller chooses a different workflow alias.
        design_hash = definition_snapshot_hash(metadata)
        definition_hash = plan.metadata.get('bpmn_definition_hash')
        if (plan.plan_hash != binding.plan_hash or plan.policy_version != binding.policy_version
                or plan.workflow_id != binding.workflow_id or not design_hash
                or not isinstance(definition_hash, str) or re.fullmatch(r'[a-f0-9]{64}', definition_hash) is None):
            raise ValueError('binding_integrity')
    except (ValueError, TypeError, UnicodeError) as exc:
        raise BpmnDefinitionSnapshotError('workflow_bpmn_definition_binding_invalid') from exc
    return {'schema': 'ananta.bpmn_definition_snapshot.v1', 'workflow_id': binding.workflow_id,
            'run_id': binding.run_id, 'plan_hash': binding.plan_hash, 'definition_hash': definition_hash,
            'source_sha256': source_hash, 'bpmn_xml': source}
