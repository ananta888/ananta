import type { BpmnCommandBinding, BpmnEdgeTrace, BpmnWorkflowCommand, WorkflowStatus } from './visual-process-api.service';

export interface BpmnRuntimeElement {
  id: string;
  status: string;
  elementId: string;
  taskId: string;
  activationId: string;
  iteration: string;
}

export interface BpmnRuntimeView {
  workflowId: string;
  backend: string;
  status: string;
  runId: string;
  planHash: string;
  revision: string;
  definitionHash: string;
  steps: BpmnRuntimeElement[];
  edges: { id: string; status: string; source: string }[];
  commands: BpmnWorkflowCommand[];
  commandBinding: BpmnCommandBinding | null;
}

export function bpmnRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

const text = (value: unknown): string => typeof value === 'string' ? value : '';
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const COMMANDS: BpmnWorkflowCommand[] = ['resume', 'retry', 'cancel'];

function stepView(step: Record<string, unknown>): BpmnRuntimeElement {
  return {
    id: text(step['step_id']), status: text(step['status']),
    elementId: text(step['element_id']) || text(step['step_id']),
    taskId: text(step['hub_task_id']), activationId: text(step['activation_id']),
    iteration: integer(step['iteration']) ? String(step['iteration']) : '',
  };
}

/** The second status read fences independently loaded history against concurrent commands. */
export function sameBpmnRuntimeSnapshot(a: WorkflowStatus, b: WorkflowStatus): boolean {
  return ['tenant_id', 'workflow_id', 'run_id', 'plan_hash', 'definition_hash', 'revision', 'checkpoint_ref', 'status']
    .every(key => a[key] === b[key]);
}

export function bpmnCommandBinding(status: WorkflowStatus): BpmnCommandBinding | null {
  return integer(status['revision']) && text(status['run_id']) && text(status['plan_hash']) && text(status['checkpoint_ref'])
    ? { expected_revision: status['revision'], run_id: text(status['run_id']),
      plan_hash: text(status['plan_hash']), checkpoint_ref: text(status['checkpoint_ref']) }
    : null;
}

/** A closed projection of Hub fields; no local inference of execution or evidence. */
export function bpmnRuntimeView(status: WorkflowStatus, events: unknown[] = [], trace?: BpmnEdgeTrace | null): BpmnRuntimeView {
  const steps = new Map<string, BpmnRuntimeElement>();
  for (const value of Array.isArray(status.steps) ? status.steps : []) {
    const step = bpmnRecord(value);
    if (text(step['step_id']) && text(step['status'])) steps.set(text(step['step_id']), stepView(step));
  }
  const edges = new Map<string, BpmnRuntimeView['edges'][number]>();
  // A trace without an exact run/revision binding cannot colour this snapshot.
  if (trace?.schema === 'ananta.caseflow_edge_trace_read_model.v1' && trace.workflow_id === status.workflow_id
    && trace.run_id === status['run_id'] && integer(status['revision']) && trace.source_revision === status['revision']) {
    for (const value of Array.isArray(trace.edges) ? trace.edges : []) {
      const edge = bpmnRecord(value);
      if (text(edge['edge_id']) && edge['verification_status'] === 'verified'
        && ['active', 'inactive'].includes(text(edge['activity_status']))) {
        edges.set(text(edge['edge_id']), { id: text(edge['edge_id']), status: text(edge['activity_status']), source: 'Hub edge trace' });
      }
    }
  }
  const seen = new Set<string>();
  const provenance = new Map<string, Partial<BpmnRuntimeElement>>();
  const cursor = status['event_cursor'];
  const lastSequence = typeof cursor === 'string' && /^\d+$/.test(cursor) ? Number(cursor) : cursor;
  const canonical = (Array.isArray(events) ? events : []).map(bpmnRecord)
    .filter(event => event['schema'] === 'ananta.workflow_event.v1' && text(status['run_id'])
      && event['workflow_id'] === status.workflow_id && event['run_id'] === status['run_id']
      && (!status['tenant_id'] || event['tenant_id'] === status['tenant_id'])
      && (cursor === undefined || integer(lastSequence) && Number(event['sequence']) <= lastSequence)
      && text(event['event_id']) && integer(event['sequence']))
    .sort((a, b) => Number(a['sequence']) - Number(b['sequence']));
  for (const event of canonical) {
    const eventId = text(event['event_id']);
    if (seen.has(eventId)) continue;
    seen.add(eventId);
    const payload = bpmnRecord(event['payload']);
    if (payload['definition_hash'] && payload['definition_hash'] !== status['definition_hash']) continue;
    if (payload['plan_hash'] && payload['plan_hash'] !== status['plan_hash']) continue;
    const stepId = text(event['step_id']);
    const step = steps.get(stepId);
    // Status owns the step outcome; historical events add explicit provenance only.
    if (step) {
      const fields = provenance.get(stepId) || {};
      if (text(payload['hub_task_id'])) fields.taskId = text(payload['hub_task_id']);
      if (text(payload['activation_id'])) fields.activationId = text(payload['activation_id']);
      if (integer(payload['iteration'])) fields.iteration = String(payload['iteration']);
      provenance.set(stepId, fields);
    }
    if (event['event_type'] === 'workflow.step.completed' && text(payload['selected_edge'])) {
      const id = text(payload['selected_edge']);
      edges.set(id, { id, status: 'selected', source: 'Hub gateway event' });
    }
  }
  for (const [id, fields] of provenance) {
    const step = steps.get(id)!;
    if (!step.taskId) step.taskId = fields.taskId || '';
    if (!step.activationId) step.activationId = fields.activationId || '';
    if (!step.iteration) step.iteration = fields.iteration || '';
  }
  const commandBinding = bpmnCommandBinding(status);
  const allowedCommands = status['allowed_commands'];
  return {
    workflowId: text(status.workflow_id),
    backend: text(status.backend),
    status: text(status.status),
    runId: text(status['run_id']),
    planHash: text(status['plan_hash']),
    revision: integer(status['revision']) ? String(status['revision']) : '',
    definitionHash: text(status['definition_hash']),
    steps: [...steps.values()], edges: [...edges.values()], commandBinding,
    commands: commandBinding && Array.isArray(allowedCommands)
      ? COMMANDS.filter(command => allowedCommands.includes(command)) : [],
  };
}

/** Only closed, known state names become CSS markers. Unknown states remain textual. */
export function bpmnRuntimeMarker(status: string): string {
  const markers: Record<string, string> = {
    selected: 'selected', active: 'running', running: 'running', delegated: 'running',
    waiting: 'waiting', pending: 'waiting', awaiting_approval: 'waiting', waiting_for_approval: 'waiting',
    skipped: 'skipped', completed: 'completed', succeeded: 'completed',
    failed: 'failed', blocked: 'blocked', cancelled: 'cancelled',
  };
  return Object.hasOwn(markers, status) ? 'bpmn-runtime-' + markers[status] : '';
}
