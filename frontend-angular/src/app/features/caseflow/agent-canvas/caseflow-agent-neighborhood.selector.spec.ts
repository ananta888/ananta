import { describe, expect, it } from 'vitest';
import type {
  VpEdge,
  VpGraph,
  VpStep,
} from '../../visual-process/visual-process-api.service';
import { selectCaseFlowAgentNeighborhood } from './caseflow-agent-neighborhood.selector';

describe('selectCaseFlowAgentNeighborhood', () => {
  it('keeps both directions of a bidirectional relationship separate and canonical', () => {
    const graph = fixtureGraph();

    const result = selectCaseFlowAgentNeighborhood(graph, 'selected');

    expect(result).toEqual({
      ok: true,
      issues: [],
      value: {
        step_id: 'selected',
        parents: [
          {
            edge_id: 'edge-a-selected',
            peer_step_id: 'agent-a',
            peer_label: 'Agent A',
            peer_role: 'researcher',
          },
          {
            edge_id: 'edge-b-selected',
            peer_step_id: 'agent-b',
            peer_label: 'Agent B',
            peer_role: 'reviewer',
          },
        ],
        children: [
          {
            edge_id: 'edge-selected-a',
            peer_step_id: 'agent-a',
            peer_label: 'Agent A',
            peer_role: 'researcher',
          },
          {
            edge_id: 'edge-selected-c',
            peer_step_id: 'agent-c',
            peer_label: 'Agent C',
            peer_role: 'custom',
          },
        ],
        loops: [
          { edge_id: 'loop-a', label: 'retry' },
          { edge_id: 'loop-z' },
        ],
      },
    });
  });

  it('is deterministic regardless of graph step and edge ordering', () => {
    const graph = fixtureGraph();
    const reordered: VpGraph = {
      ...graph,
      steps: [...graph.steps].reverse(),
      edges: [...graph.edges].reverse(),
    };

    const original = selectCaseFlowAgentNeighborhood(graph, 'selected');
    const reversed = selectCaseFlowAgentNeighborhood(reordered, 'selected');

    expect(reversed).toEqual(original);
  });

  it('does not mutate the graph while deriving display roles and ordering', () => {
    const graph = fixtureGraph();
    const before = JSON.stringify(graph);

    const result = selectCaseFlowAgentNeighborhood(graph, 'selected');

    expect(result.ok).toBe(true);
    expect(JSON.stringify(graph)).toBe(before);
    expect(graph.edges.map(edge => edge.id)).toEqual([
      'edge-selected-c',
      'loop-z',
      'edge-b-selected',
      'edge-selected-a',
      'loop-a',
      'edge-a-selected',
      'unrelated',
    ]);
  });

  it('fails closed with a typed issue when the selected step is missing', () => {
    const result = selectCaseFlowAgentNeighborhood(fixtureGraph(), 'missing');

    expect(result).toEqual({
      ok: false,
      issues: [{
        code: 'agent_step_not_found',
        path: '/steps/missing',
        message: 'Agent step "missing" was not found.',
      }],
    });
  });

  it('fails closed when an incident edge references an unknown peer', () => {
    const graph = fixtureGraph();
    graph.edges = [edge('dangling-child', 'selected', 'missing-child')];

    const result = selectCaseFlowAgentNeighborhood(graph, 'selected');

    expect(result).toEqual({
      ok: false,
      issues: [{
        code: 'agent_step_not_found',
        path: '/edges/dangling-child/target',
        message: 'Edge "dangling-child" references unknown step "missing-child".',
      }],
    });
  });

  it('ignores unrelated graph edges and returns empty collections for an isolated step', () => {
    const graph = fixtureGraph();
    graph.steps.push(step('isolated', 'Isolated'));

    const result = selectCaseFlowAgentNeighborhood(graph, 'isolated');

    expect(result).toEqual({
      ok: true,
      issues: [],
      value: {
        step_id: 'isolated',
        parents: [],
        children: [],
        loops: [],
      },
    });
  });
});

function fixtureGraph(): VpGraph {
  return {
    id: 'neighborhood',
    name: 'Neighborhood',
    description: 'Directed neighborhood fixture',
    version: '1',
    tags: [],
    steps: [
      step('agent-c', 'Agent C', '   '),
      step('selected', 'Selected', 'lead'),
      step('agent-b', 'Agent B', 'reviewer'),
      step('agent-a', 'Agent A', 'researcher'),
    ],
    edges: [
      edge('edge-selected-c', 'selected', 'agent-c'),
      edge('loop-z', 'selected', 'selected'),
      edge('edge-b-selected', 'agent-b', 'selected'),
      edge('edge-selected-a', 'selected', 'agent-a'),
      edge('loop-a', 'selected', 'selected', 'retry'),
      edge('edge-a-selected', 'agent-a', 'selected'),
      edge('unrelated', 'agent-b', 'agent-c'),
    ],
  };
}

function step(id: string, label: string, role?: string): VpStep {
  return {
    id,
    label,
    kind: 'coding',
    ...(role === undefined ? {} : { role }),
    io: { inputs: [], outputs: [] },
    position: { x: 0, y: 0 },
    policy_hints: [],
    gate: false,
  };
}

function edge(
  id: string,
  source: string,
  target: string,
  label?: string,
): VpEdge {
  return {
    id,
    source,
    target,
    condition: { kind: 'always' },
    ...(label === undefined ? {} : { label }),
  };
}
