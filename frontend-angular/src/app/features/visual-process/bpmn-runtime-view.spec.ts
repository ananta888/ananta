import { describe, expect, it } from 'vitest';
import { bpmnCanvasStates, bpmnRuntimeView, sameBpmnRuntimeSnapshot } from './bpmn-runtime-view';
import type { WorkflowStatus } from './visual-process-api.service';

const status: WorkflowStatus = { schema: 'synthetic', backend: 'ananta-native', workflow_id: 'workflow',
  run_id: 'synthetic-run', tenant_id: 'synthetic-tenant', plan_hash: 'synthetic-plan', definition_hash: 'synthetic-definition',
  status: 'running', revision: 3, steps: [{ step_id: 'Task_1', status: 'completed' }], checkpoint_ref: 'synthetic-checkpoint' };
const event = { schema: 'ananta.workflow_event.v1', event_id: 'synthetic-event', sequence: 1,
  workflow_id: status.workflow_id, run_id: status['run_id'], tenant_id: status['tenant_id'], step_id: 'Task_1',
  event_type: 'workflow.step.completed', payload: { selected_edge: 'Flow_1', hub_task_id: 'synthetic-task' } };

describe('BPMN Hub runtime projection', () => {
  it('keeps a reopened pending edge over historical gateway selection', () => {
    const trace = { schema: 'ananta.caseflow_edge_trace_read_model.v1', workflow_id: status.workflow_id,
      run_id: String(status['run_id']), source_revision: 3, edges: [{ edge_id: 'Flow_1',
        verification_status: 'verified', edge_status: 'pending' }] };
    const view = bpmnRuntimeView(status, [event], trace);
    expect(view.edges).toEqual([{ id: 'Flow_1', status: 'pending', source: 'Hub edge trace' }]);
  });
  it('shows current cancellation without erasing accepted completion or reviving an older selection', () => {
    const trace = { schema: 'ananta.caseflow_edge_trace_read_model.v1', workflow_id: status.workflow_id,
      run_id: String(status['run_id']), source_revision: 3, edges: [
        { edge_id: 'Flow_1', verification_status: 'verified', edge_status: 'cancelled' },
        { edge_id: 'Flow_2', verification_status: 'verified', edge_status: 'completed' },
      ] };
    const view = bpmnRuntimeView({ ...status, status: 'cancelled' }, [event], trace);
    expect(view.edges.map(({ id, status }) => ({ id, status }))).toEqual([
      { id: 'Flow_1', status: 'cancelled' }, { id: 'Flow_2', status: 'completed' },
    ]);
    expect(bpmnCanvasStates(view)).toContainEqual({ id: 'Flow_1', status: 'cancelled' });
  });

  it('preserves current explicit edge outcomes over older gateway selections', () => {
    const trace = { schema: 'ananta.caseflow_edge_trace_read_model.v1', workflow_id: status.workflow_id,
      run_id: String(status['run_id']), source_revision: 3, edges: [{ edge_id: 'Flow_1',
        verification_status: 'verified', activity_status: 'inactive', edge_status: 'completed' }] };
    const view = bpmnRuntimeView(status, [event], trace);
    expect(view.edges).toEqual([{ id: 'Flow_1', status: 'completed', source: 'Hub edge trace' }]);
  });
  it('keeps source/iteration lineage and suppresses ambiguous or incomplete collapsed edges', () => {
    const trace = { schema: 'ananta.caseflow_edge_trace_read_model.v1', workflow_id: status.workflow_id,
      run_id: String(status['run_id']), source_revision: 3, edges: [0, 1].map(iteration => ({
        edge_id: `activation-${iteration}`, verification_status: 'verified', activity_status: 'inactive',
        bpmn_provenance: { schema: 'ananta.bpmn_edge_provenance.v1', activation_id: `activation-${iteration}`,
          element_id: 'original-flow', element_kind: 'sequence_flow', iteration, activation_count: 2 },
      })) };
    const view = bpmnRuntimeView(status, [], trace);
    expect(view.edges[0]).toMatchObject({ elementId: 'original-flow', iteration: '0', activationCount: 2 });
    expect(bpmnCanvasStates(view)).toContainEqual({ id: 'original-flow', status: 'inactive' });
    expect(bpmnCanvasStates({ ...view, edges: view.edges.slice(0, 1) })).not.toContainEqual(
      { id: 'original-flow', status: 'inactive' });
    expect(bpmnCanvasStates({ ...view, edges: [view.edges[0], { ...view.edges[1], status: 'active' }] })
      .some(edge => edge.id === 'original-flow')).toBe(false);
  });

  it('does not colour one collapsed activity as both completed and waiting', () => {
    const view = bpmnRuntimeView({ ...status, steps: [
      { step_id: 'iteration-0', element_id: 'original-loop', status: 'completed' },
      { step_id: 'iteration-1', element_id: 'original-loop', status: 'pending' },
    ] });
    expect(view.steps).toHaveLength(2);
    expect(bpmnCanvasStates(view)).toEqual([]);
  });
  it('shows only explicit status fields, preserving skipped/blocked states and no variables', () => {
    const view = bpmnRuntimeView({
      schema: 'synthetic', backend: 'ananta-native', workflow_id: 'workflow', status: 'running',
      run_id: 'hub-issued', plan_hash: 'hash-from-hub', revision: 3,
      variables: { secret: 'must not enter view' },
      steps: [{ step_id: 'skipped', status: 'skipped' }, { step_id: 'blocked', status: 'blocked' },
        { step_id: 'no-status' }, null, { status: 'succeeded' }],
    });
    expect(view.steps.map(({ id, status }) => ({ id, status }))).toEqual([{ id: 'skipped', status: 'skipped' }, { id: 'blocked', status: 'blocked' }]);
    expect(view.planHash).toBe('hash-from-hub');
    expect(view.revision).toBe('3');
    expect(JSON.stringify(view)).not.toContain('secret');
  });

  it('does not infer run identity, revision or evidence from legacy running', () => {
    expect(bpmnRuntimeView({ schema: 'synthetic', backend: 'legacy', workflow_id: 'workflow', status: 'running' }))
      .toEqual({ workflowId: 'workflow', backend: 'legacy', status: 'running', runId: '', planHash: '', revision: '',
        definitionHash: '', steps: [], edges: [], commands: [], commandBinding: null });
  });

  it('rebuilds selected edges and task identity from canonical events without duplicate rows', () => {
    const view = bpmnRuntimeView(status, [event, event]);
    expect(view.edges).toEqual([{ id: 'Flow_1', status: 'selected', source: 'Hub gateway event' }]);
    expect(view.steps[0]).toMatchObject({ id: 'Task_1', taskId: 'synthetic-task', status: 'completed' });
  });

  it.each([
    { ...event, run_id: 'other' }, { ...event, tenant_id: 'other' }, { ...event, workflow_id: 'other' },
    { ...event, schema: 'unknown' }, { ...event, event_id: '' },
    { ...event, payload: { ...event.payload, definition_hash: 'other' } },
  ])('ignores events with mismatched or incomplete bindings', invalid => {
    expect(bpmnRuntimeView(status, [invalid]).edges).toEqual([]);
  });

  it('never overwrites current step status with historical completions', () => {
    const view = bpmnRuntimeView({ ...status, steps: [{ step_id: 'Task_1', status: 'failed', element_id: 'Source_1',
      activation_id: 'synthetic-activation', iteration: 0 }] }, [event]);
    expect(view.steps[0]).toMatchObject({ status: 'failed', elementId: 'Source_1', activationId: 'synthetic-activation', iteration: '0' });
  });

  it('only accepts edge traces from the exact run and status revision', () => {
    const trace = { schema: 'ananta.caseflow_edge_trace_read_model.v1', workflow_id: status.workflow_id,
      run_id: String(status['run_id']), source_revision: 3, edges: [
        { edge_id: 'Flow_1', verification_status: 'verified', activity_status: 'active', variables: { secret: 'HIDDEN' } },
        { edge_id: 'Flow_2', verification_status: 'unverified', activity_status: 'active' },
      ] };
    expect(bpmnRuntimeView(status, [], trace).edges).toHaveLength(1);
    expect(JSON.stringify(bpmnRuntimeView(status, [], trace))).not.toContain('HIDDEN');
    expect(bpmnRuntimeView(status, [], { ...trace, source_revision: 2 }).edges).toEqual([]);
    expect(bpmnRuntimeView(status, [], { ...trace, run_id: 'other' }).edges).toEqual([]);
  });

  it('requires complete command bindings and explicit commands', () => {
    expect(bpmnRuntimeView(status).commands).toEqual([]);
    expect(bpmnRuntimeView({ ...status, allowed_commands: ['cancel', 'unknown'] }).commands).toEqual(['cancel']);
    expect(bpmnRuntimeView({ ...status, allowed_commands: ['cancel'], checkpoint_ref: '' }).commands).toEqual([]);
    expect(bpmnRuntimeView({ ...status, allowed_commands: ['cancel'], revision: -1 }).commands).toEqual([]);
    expect(bpmnRuntimeView({ ...status, allowed_commands: ['pause', 'cancel'] }).commands).toEqual(['pause', 'cancel']);
    expect(bpmnRuntimeView({ ...status, allowed_commands: ['pause'], checkpoint_ref: '' }).commands).toEqual([]);
  });

  it('uses the exact execution binding alongside a different compatibility public revision/checkpoint', () => {
    const command_binding = { schema: 'ananta.workflow_command_binding.v1', expected_revision: 13,
      run_id: status['run_id'], plan_hash: status['plan_hash'], checkpoint_ref: 'wfc-' + 'a'.repeat(32) };
    const bound = { ...status, allowed_commands: ['pause'], command_binding };
    expect(bpmnRuntimeView(bound).commandBinding).toMatchObject({ expected_revision: 13,
      checkpoint_ref: command_binding.checkpoint_ref });
    expect(sameBpmnRuntimeSnapshot(bound, { ...bound, command_binding: { ...command_binding,
      expected_revision: 14 } })).toBe(false);
    for (const changed of [{ expected_revision: true }, { expected_revision: 0 },
      { run_id: 'foreign' }, { plan_hash: 'foreign' }, { checkpoint_ref: '' }]) {
      expect(bpmnRuntimeView({ ...bound, command_binding: { ...command_binding, ...changed } }).commands).toEqual([]);
    }
  });

  it('rejects history beyond the status cursor and preserves current task provenance', () => {
    expect(bpmnRuntimeView({ ...status, event_cursor: '0' }, [event]).edges).toEqual([]);
    expect(bpmnRuntimeView({ ...status, event_cursor: 'invalid' }, [event]).edges).toEqual([]);
    const view = bpmnRuntimeView({ ...status, event_cursor: '1', steps: [
      { step_id: 'Task_1', status: 'running', hub_task_id: 'newer-task' },
    ] }, [event]);
    expect(view.edges).toHaveLength(1);
    expect(view.steps[0].taskId).toBe('newer-task');
  });

  it('fences reload against same-workflow run, plan, checkpoint and revision changes', () => {
    expect(sameBpmnRuntimeSnapshot(status, { ...status })).toBe(true);
    for (const key of ['run_id', 'plan_hash', 'definition_hash', 'revision', 'checkpoint_ref']) {
      expect(sameBpmnRuntimeSnapshot(status, { ...status, [key]: 'changed' })).toBe(false);
    }
  });
});
