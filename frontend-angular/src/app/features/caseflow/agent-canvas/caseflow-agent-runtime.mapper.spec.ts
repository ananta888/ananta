import { describe, expect, it } from 'vitest';
import type { VpRuntimeOverlay } from '../../visual-process/visual-process-api.service';
import type { CaseFlowAgentCanvasNodeProjection } from './caseflow-agent-canvas.models';
import { projectCaseFlowAgentRuntime } from './caseflow-agent-runtime.mapper';

describe('projectCaseFlowAgentRuntime', () => {
  it('projects current steps and every supported display status without mutating inputs', () => {
    const nodes = STATUSES.map((status, index) => node(`agent-${index}`));
    const runtime = overlay(Object.fromEntries(
      STATUSES.map((status, index) => [`agent-${index}`, { step_id: `agent-${index}`, status }]),
    ), ['agent-1', 'agent-2']);
    const runtimeBefore = JSON.stringify(runtime);
    const nodesBefore = JSON.stringify(nodes);

    const result = projectCaseFlowAgentRuntime('workflow-existing', nodes, runtime);

    expect(Object.values(result.nodes).map(item => item.status)).toEqual([
      'pending',
      'running',
      'awaiting_approval',
      'success',
      'error',
      'skipped',
      'cancelled',
      'unknown',
    ]);
    expect(result.current_step_ids).toEqual(['agent-1', 'agent-2']);
    expect(result.nodes['agent-1'].active).toBe(true);
    expect(result.nodes['agent-2'].active).toBe(true);
    expect(JSON.stringify(runtime)).toBe(runtimeBefore);
    expect(JSON.stringify(nodes)).toBe(nodesBefore);
  });

  it('keeps unknown current state explicit and never guesses an active edge', () => {
    const result = projectCaseFlowAgentRuntime(
      'workflow-existing',
      [node('agent-unknown')],
      overlay({
        'agent-unknown': { step_id: 'agent-unknown', status: 'unknown' },
      }, ['agent-unknown', 'missing-agent']),
    );

    expect(result.nodes['agent-unknown']).toMatchObject({
      status: 'unknown',
      label: 'Unbekannt',
      current: true,
      active: false,
    });
    expect(result.current_step_ids).toEqual(['agent-unknown']);
    expect(result.active_edge_ids).toEqual([]);
  });

  it('returns an unavailable unknown projection without runtime data', () => {
    const result = projectCaseFlowAgentRuntime('workflow-existing', [node('agent-a')], null);

    expect(result.available).toBe(false);
    expect(result.nodes['agent-a'].status).toBe('unknown');
    expect(result.nodes['agent-a'].active).toBe(false);
  });

  it('fails closed when runtime belongs to another graph or process', () => {
    const staleWorkflow = overlay({
      'agent-a': { step_id: 'agent-a', status: 'running' },
    }, ['agent-a']);
    const staleProcess = { ...staleWorkflow, process_id: 'another-graph' };

    expect(projectCaseFlowAgentRuntime('current-graph', [node('agent-a')], staleWorkflow))
      .toMatchObject({ available: false, current_step_ids: [], active_edge_ids: [] });
    expect(projectCaseFlowAgentRuntime('workflow-existing', [node('agent-a')], staleProcess))
      .toMatchObject({ available: false, current_step_ids: [], active_edge_ids: [] });
  });

  it('fails closed when a runtime step contradicts its map key', () => {
    const runtime = overlay({
      'agent-a': { step_id: 'agent-b', status: 'running' },
    }, ['agent-a']);

    const result = projectCaseFlowAgentRuntime('workflow-existing', [node('agent-a')], runtime);

    expect(result.available).toBe(false);
    expect(result.nodes['agent-a']).toMatchObject({ status: 'unknown', current: false, active: false });
    expect(result.current_step_ids).toEqual([]);
  });
});

const STATUSES = [
  'pending',
  'running',
  'awaiting_approval',
  'succeeded',
  'failed',
  'skipped',
  'cancelled',
  'unknown',
] as const;

function node(stepId: string): CaseFlowAgentCanvasNodeProjection {
  return {
    step_id: stepId,
    label: stepId,
    role: 'developer',
    role_preset: 'developer',
    icon: 'code',
    position: { x: 0, y: 0 },
    configuration: {
      context_bindings: [],
      model_routing: {},
      policy_hints: [],
      human_gate: false,
    },
    incoming_edge_ids: [],
    outgoing_edge_ids: [],
  };
}

function overlay(
  steps: VpRuntimeOverlay['steps'],
  currentStepIds: string[],
): VpRuntimeOverlay {
  return {
    run_id: 'run-existing',
    workflow_id: 'workflow-existing',
    overall_status: 'running',
    current_step_ids: currentStepIds,
    steps,
    updated_at: 1,
  };
}
