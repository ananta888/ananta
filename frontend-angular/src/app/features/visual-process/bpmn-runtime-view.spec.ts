import { describe, expect, it } from 'vitest';
import { bpmnRuntimeView, sameBpmnRuntimeSnapshot } from './bpmn-runtime-view';
import type { WorkflowStatus } from './visual-process-api.service';

const status: WorkflowStatus = { schema: 'synthetic', backend: 'ananta-native', workflow_id: 'workflow',
  run_id: 'synthetic-run', tenant_id: 'synthetic-tenant', plan_hash: 'synthetic-plan', definition_hash: 'synthetic-definition',
  status: 'running', revision: 3, steps: [{ step_id: 'Task_1', status: 'completed' }], checkpoint_ref: 'synthetic-checkpoint' };
const event = { schema: 'ananta.workflow_event.v1', event_id: 'synthetic-event', sequence: 1,
  workflow_id: status.workflow_id, run_id: status['run_id'], tenant_id: status['tenant_id'], step_id: 'Task_1',
  event_type: 'workflow.step.completed', payload: { selected_edge: 'Flow_1', hub_task_id: 'synthetic-task' } };

describe('BPMN Hub runtime projection', () => {
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
