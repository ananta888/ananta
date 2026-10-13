"""Worker-only composition of authenticated evidence read and existing execution."""

from worker.runtime.native_graph.pi_evidence import HubPiEvidenceReader, VerifiedPiEvidenceConsumer
from worker.runtime.workflow_adapter_task_execution import consume_delegated_workflow_task
from worker.runtime.workflow_hub_gateway import HttpWorkflowHubDecisionClient


def consume_pi_evidence_task(task, evidence_assignment):
    client = HttpWorkflowHubDecisionClient.from_environment()
    if client is None:
        raise ValueError('pi_worker_evidence_transport_unavailable')
    consumer = VerifiedPiEvidenceConsumer(reader=HubPiEvidenceReader(client), execute=consume_delegated_workflow_task)
    return consumer(task, evidence_assignment)
