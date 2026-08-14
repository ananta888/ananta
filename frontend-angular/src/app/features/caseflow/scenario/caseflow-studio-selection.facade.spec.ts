import { describe, expect, it } from 'vitest';

import type { VpEdge, VpGraph, VpStep } from '../../visual-process/visual-process-api.service';
import { CaseFlowStudioSelectionFacade } from './caseflow-studio-selection.facade';

describe('CaseFlowStudioSelectionFacade', () => {
  it('keeps a node and an edge with the same ID unambiguous', () => {
    const graph = graphWith([
      edge('shared', 'shared', 'peer'),
    ]);
    const selection = new CaseFlowStudioSelectionFacade();

    selection.selectNode(graph, 'shared');
    expect(selection.selection()).toEqual({
      kind: 'node', graph_id: 'graph-a', step_id: 'shared',
    });
    expect(selection.selectedNodeId()).toBe('shared');
    expect(selection.selectedEdge()).toBeNull();

    selection.selectEdge(graph, {
      edge_id: 'shared', source_step_id: 'shared', target_step_id: 'peer',
    });
    expect(selection.selection()?.kind).toBe('edge');
    expect(selection.selectedNodeId()).toBeNull();
    expect(selection.selectedEdge()).toEqual({
      edge_id: 'shared', source_step_id: 'shared', target_step_id: 'peer',
    });
  });

  it('selects an edge only by its full exact identity', () => {
    const selection = new CaseFlowStudioSelectionFacade();
    const graph = graphWith([edge('forward', 'shared', 'peer')]);

    selection.selectEdge(graph, {
      edge_id: 'forward', source_step_id: 'peer', target_step_id: 'shared',
    });

    expect(selection.selection()).toBeNull();
  });

  it('exposes a reverse only when the swapped direction is exact and unique', () => {
    const selection = new CaseFlowStudioSelectionFacade();
    const graph = graphWith([
      edge('forward', 'shared', 'peer'),
      edge('reverse', 'peer', 'shared'),
    ]);

    selection.selectEdge(graph, {
      edge_id: 'forward', source_step_id: 'shared', target_step_id: 'peer',
    });

    expect(selection.reverseEdge()).toEqual({
      edge_id: 'reverse', source_step_id: 'peer', target_step_id: 'shared',
    });
  });

  it('fails closed for ambiguous reverse edges and never treats a loop as its own reverse', () => {
    const selection = new CaseFlowStudioSelectionFacade();
    const ambiguous = graphWith([
      edge('forward', 'shared', 'peer'),
      edge('reverse-a', 'peer', 'shared'),
      edge('reverse-b', 'peer', 'shared'),
      edge('loop', 'shared', 'shared'),
    ]);

    selection.selectEdge(ambiguous, {
      edge_id: 'forward', source_step_id: 'shared', target_step_id: 'peer',
    });
    expect(selection.reverseEdge()).toBeNull();

    selection.selectEdge(ambiguous, {
      edge_id: 'loop', source_step_id: 'shared', target_step_id: 'shared',
    });
    expect(selection.reverseEdge()).toBeNull();
  });

  it('clears when graph identity or the exact selected entity drifts', () => {
    const selection = new CaseFlowStudioSelectionFacade();
    const graph = graphWith([edge('forward', 'shared', 'peer')]);
    selection.selectEdge(graph, {
      edge_id: 'forward', source_step_id: 'shared', target_step_id: 'peer',
    });

    selection.reconcileGraph({ ...graph, id: 'graph-b' });
    expect(selection.selection()).toBeNull();

    selection.selectEdge(graph, {
      edge_id: 'forward', source_step_id: 'shared', target_step_id: 'peer',
    });
    selection.reconcileGraph({
      ...graph,
      edges: [edge('forward', 'peer', 'shared')],
    });
    expect(selection.selection()).toBeNull();

    selection.selectNode(graph, 'shared');
    selection.reconcileGraph({
      ...graph,
      steps: graph.steps.filter(step => step.id !== 'shared'),
    });
    expect(selection.selection()).toBeNull();
  });

  it('reconciles a still-exact edge and refreshes its unique reverse identity', () => {
    const selection = new CaseFlowStudioSelectionFacade();
    const graph = graphWith([edge('forward', 'shared', 'peer')]);
    selection.selectEdge(graph, {
      edge_id: 'forward', source_step_id: 'shared', target_step_id: 'peer',
    });

    selection.reconcileGraph({
      ...graph,
      edges: [...graph.edges, edge('reverse', 'peer', 'shared')],
    });

    expect(selection.reverseEdge()).toEqual({
      edge_id: 'reverse', source_step_id: 'peer', target_step_id: 'shared',
    });
  });
});

function graphWith(edges: readonly VpEdge[]): VpGraph {
  return {
    id: 'graph-a',
    name: 'Selection graph',
    description: '',
    version: '1',
    tags: [],
    steps: [step('shared'), step('peer')],
    edges: [...edges],
  };
}

function step(id: string): VpStep {
  return {
    id,
    label: id,
    role: 'developer',
    kind: 'coding',
    io: { inputs: [], outputs: [] },
    position: { x: 0, y: 0 },
    policy_hints: [],
    gate: false,
  };
}

function edge(id: string, source: string, target: string): VpEdge {
  return { id, source, target, condition: { kind: 'always' } };
}
