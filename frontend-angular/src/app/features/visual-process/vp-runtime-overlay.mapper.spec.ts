import { describe, expect, it } from 'vitest';

import {
  VpRuntimeOverlayContractError,
  decodeVpWorkflowStatus,
} from './vp-runtime-overlay.mapper';

describe('decodeVpWorkflowStatus', () => {
  it('decodes an exact graph/run scope and normalizes supported Hub aliases', () => {
    const decoded = decodeVpWorkflowStatus({
      schema: 'ananta.workflow_backend_status.v1',
      backend: 'hub',
      workflow_id: 'graph-a',
      run_id: 'run-a',
      process_id: 'graph-a',
      process_version: '7',
      snapshot_hash: 'hash-a',
      revision: 4,
      status: 'RUNNING',
      updated_at: 19,
      steps: [
        { step_id: 'lead', run_state: ' COMPLETED ' },
        { step_id: 'builder', status: 'IN_PROGRESS' },
        { step_id: 'critic', run_state: 'waiting_for_review' },
      ],
    }, {
      workflow_id: 'graph-a',
      graph_id: 'graph-a',
      run_id: 'run-a',
      require_revision: true,
    });

    expect(decoded.kind).toBe('runtime');
    if (decoded.kind !== 'runtime') throw new Error('runtime expected');
    expect(decoded.revision).toBe(4);
    expect(decoded.terminal).toBe(false);
    expect(decoded.overlay).toMatchObject({
      workflow_id: 'graph-a',
      run_id: 'run-a',
      process_id: 'graph-a',
      overall_status: 'RUNNING',
      current_step_ids: ['builder', 'critic'],
      updated_at: 19,
    });
    expect(decoded.overlay.steps['lead'].status).toBe('succeeded');
    expect(decoded.overlay.steps['builder'].status).toBe('running');
    expect(decoded.overlay.steps['critic'].status).toBe('awaiting_approval');
    expect(Object.isFrozen(decoded.overlay)).toBe(true);
  });

  it('rejects a missing Hub update time instead of synthesizing activity evidence', () => {
    const raw = runtimeStatus();
    delete raw['updated_at'];

    expect(() => decodeVpWorkflowStatus(raw, {}))
      .toThrowError('vp_runtime_updated_at_invalid');
  });

  it('recognizes an exact not-found status without inventing a run identity', () => {
    expect(decodeVpWorkflowStatus({
      schema: 'ananta.workflow_backend_status.v1',
      workflow_id: 'graph-a',
      status: 'not_found',
      steps: [],
    }, {
      workflow_id: 'graph-a',
      graph_id: 'graph-a',
      require_revision: true,
    })).toEqual({ kind: 'no_run', workflow_id: 'graph-a' });
  });

  it.each([
    [{ ...runtimeStatus(), run_id: undefined }, 'vp_runtime_run_id_invalid'],
    [{ ...runtimeStatus(), schema: undefined }, 'vp_runtime_schema_unsupported'],
    [{ ...runtimeStatus(), schema: 'ananta.workflow_backend_status.v2' }, 'vp_runtime_schema_unsupported'],
    [{ ...runtimeStatus(), run_id: 'graph-a' }, 'vp_runtime_run_scope_mismatch'],
    [{ ...runtimeStatus(), workflow_id: 'graph-b' }, 'vp_runtime_workflow_scope_mismatch'],
    [{ ...runtimeStatus(), process_id: 'graph-b' }, 'vp_runtime_graph_scope_mismatch'],
    [{ ...runtimeStatus(), revision: undefined }, 'vp_runtime_revision_required'],
    [{ ...runtimeStatus(), status: 'surprising' }, 'vp_runtime_status_invalid'],
    [{ ...runtimeStatus(), steps: [{ step_id: 'lead', status: 'surprising' }] }, 'vp_runtime_status_invalid'],
    [{ ...runtimeStatus(), steps: [{ step_id: 'lead', status: 'running', gate: false }] }, 'vp_runtime_step_gate_invalid'],
    [{ ...runtimeStatus(), steps: [{ step_id: 'lead', run_state: 'running', status: 'failed' }] }, 'vp_runtime_step_status_conflict'],
    [{ ...runtimeStatus(), steps: [step('lead'), step('lead')] }, 'vp_runtime_duplicate_step_id'],
  ])('fails closed for an invalid or mismatched runtime contract: %s', (raw, reason) => {
    expect(() => decodeVpWorkflowStatus(raw, {
      workflow_id: 'graph-a',
      graph_id: 'graph-a',
      run_id: 'run-a',
      require_revision: true,
    })).toThrowError(reason as string);
  });

  it('rejects a not-found envelope that nevertheless asserts a run ID', () => {
    expect(() => decodeVpWorkflowStatus({
      schema: 'ananta.workflow_backend_status.v1',
      workflow_id: 'graph-a',
      run_id: 'run-a',
      status: 'not_found',
    }, {})).toThrow(VpRuntimeOverlayContractError);
  });

  it('keeps explicit unknown status unavailable instead of guessing activity', () => {
    const decoded = decodeVpWorkflowStatus({
      ...runtimeStatus(),
      status: 'unknown',
      steps: [{ step_id: 'lead', run_state: 'unknown' }],
    }, {});

    expect(decoded.kind).toBe('runtime');
    if (decoded.kind !== 'runtime') throw new Error('runtime expected');
    expect(decoded.overlay.overall_status).toBe('unknown');
    expect(decoded.overlay.steps['lead'].status).toBe('unknown');
    expect(decoded.overlay.current_step_ids).toEqual([]);
  });

  it('keeps the exact cancel-requested acknowledgement nonterminal for continued polling', () => {
    const decoded = decodeVpWorkflowStatus({
      ...runtimeStatus(),
      revision: 2,
      status: 'cancel_requested',
    }, {
      workflow_id: 'graph-a',
      graph_id: 'graph-a',
      run_id: 'run-a',
      require_revision: true,
    });

    expect(decoded.kind).toBe('runtime');
    if (decoded.kind !== 'runtime') throw new Error('runtime expected');
    expect(decoded.normalized_status).toBe('running');
    expect(decoded.terminal).toBe(false);
    expect(decoded.overlay.overall_status).toBe('cancel_requested');
  });

  it('accepts simultaneous step status aliases only when they agree semantically', () => {
    const decoded = decodeVpWorkflowStatus({
      ...runtimeStatus(),
      steps: [{ step_id: 'lead', run_state: 'completed', status: 'succeeded' }],
    }, {});

    expect(decoded.kind).toBe('runtime');
    if (decoded.kind !== 'runtime') throw new Error('runtime expected');
    expect(decoded.overlay.steps['lead'].status).toBe('succeeded');
  });

  it.each([
    ['completed', 'running'],
    ['failed', 'waiting_for_approval'],
    ['cancelled', 'running'],
    ['skipped', 'waiting_for_review'],
    ['succeeded', 'pending'],
    ['completed', 'unknown'],
  ])(
    'rejects terminal overall status %s with contradictory step status %s',
    (overallStatus, stepStatus) => {
      expect(() => decodeVpWorkflowStatus({
        ...runtimeStatus(),
        status: overallStatus,
        steps: [{ step_id: 'lead', status: stepStatus }],
      }, {})).toThrowError('vp_runtime_terminal_step_state_conflict');
    },
  );

  it('keeps an explicitly failed step in a completed partial-failure projection', () => {
    const decoded = decodeVpWorkflowStatus({
      ...runtimeStatus(),
      status: 'completed',
      steps: [
        { step_id: 'lead', status: 'completed' },
        { step_id: 'critic', status: 'failed' },
      ],
    }, {});

    expect(decoded.kind).toBe('runtime');
    if (decoded.kind !== 'runtime') throw new Error('runtime expected');
    expect(decoded.terminal).toBe(true);
    expect(decoded.overlay.steps['critic'].status).toBe('failed');
  });
});

function runtimeStatus(): Record<string, unknown> {
  return {
    schema: 'ananta.workflow_backend_status.v1',
    workflow_id: 'graph-a',
    run_id: 'run-a',
    revision: 1,
    updated_at: 1,
    status: 'running',
    steps: [step('lead')],
  };
}

function step(stepId: string): Record<string, unknown> {
  return { step_id: stepId, status: 'pending' };
}
