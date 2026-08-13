import { describe, expect, it } from 'vitest';
import type { VpEdge, VpGraph, VpStep } from '../../visual-process/visual-process-api.service';
import {
  CASEFLOW_AGENT_CANVAS_EXTENSION,
  CASEFLOW_AGENT_CANVAS_SCHEMA_V1,
  parseAgentCanvasExtension,
  serializeAgentCanvasExtension,
} from './caseflow-agent-canvas.models';
import {
  graphFromAgentCanvasProjection,
  projectAgentCanvas,
} from './caseflow-agent-canvas.mapper';

describe('CaseFlow agent-canvas v1 projection contract', () => {
  it('preserves unknown extension bytes while editing a managed presentation field', () => {
    const graph = fixtureGraph();
    const parsed = parseAgentCanvasExtension(graph);
    if (!parsed.ok) throw new Error(JSON.stringify(parsed.issues));

    const rawBefore = parsed.value;
    const futureTopLevelBefore = JSON.stringify(rawBefore['future_contract']);
    const futureNodeBefore = JSON.stringify(rawBefore.nodes?.['lead']?.['future_node_field']);
    const unrelatedExtensionBefore = JSON.stringify(graph.extensions?.['vendor.unrelated']);

    const edited = {
      ...rawBefore,
      nodes: {
        ...rawBefore.nodes,
        lead: {
          ...rawBefore.nodes?.['lead'],
          icon: 'security',
        },
      },
    };
    const serialized = serializeAgentCanvasExtension(graph, edited);
    if (!serialized.ok) throw new Error(JSON.stringify(serialized.issues));
    const after = serialized.value.extensions?.[CASEFLOW_AGENT_CANVAS_EXTENSION] as Record<string, any>;

    expect(after['nodes']['lead']['icon']).toBe('security');
    expect(JSON.stringify(after['future_contract'])).toBe(futureTopLevelBefore);
    expect(JSON.stringify(after['nodes']['lead']['future_node_field'])).toBe(futureNodeBefore);
    expect(JSON.stringify(serialized.value.extensions?.['vendor.unrelated']))
      .toBe(unrelatedExtensionBefore);
  });

  it('roundtrips directed, bidirectional and loop edges through the canonical graph without loss', () => {
    const graph = fixtureGraph();
    const graphBytes = JSON.stringify(graph);
    const projected = projectAgentCanvas(graph);
    if (!projected.ok) throw new Error(JSON.stringify(projected.issues));

    expect(projected.value.nodes.map(node => node.step_id)).toEqual(['lead', 'builder', 'critic']);
    expect(projected.value.edges.map(edge => edge.edge_id)).toEqual([
      'lead-builder',
      'builder-lead',
      'builder-critic',
      'critic-loop',
    ]);
    expect(projected.value.edges.find(edge => edge.edge_id === 'lead-builder')?.reverse_edge_ids)
      .toEqual(['builder-lead']);
    expect(projected.value.edges.find(edge => edge.edge_id === 'builder-critic')?.reverse_edge_ids)
      .toEqual([]);
    expect(projected.value.edges.find(edge => edge.edge_id === 'critic-loop')?.loop).toBe(true);
    expect(projected.value.edges.find(edge => edge.edge_id === 'critic-loop')?.feedback).toBe(false);

    const restored = graphFromAgentCanvasProjection(projected.value);
    expect(restored).toBe(graph);
    expect(JSON.stringify(restored)).toBe(graphBytes);
    expect((restored.steps[0] as VpStep & { future_step?: unknown }).future_step)
      .toEqual({ exact: ['keep', 7] });
    expect((restored.edges[0] as VpEdge & { future_edge?: unknown }).future_edge)
      .toEqual({ correlation: 'canonical-edge-id' });
  });

  it('derives display state without copying legacy runtime state into the canvas contract', () => {
    const graph = fixtureGraph();
    graph.steps[0].run_state = 'running';
    const projected = projectAgentCanvas(graph);
    if (!projected.ok) throw new Error(JSON.stringify(projected.issues));

    expect(projected.value.canonical_graph).toBe(graph);
    expect(Object.keys(projected.value.nodes[0])).not.toContain('run_state');
    expect(Object.keys(projected.value.nodes[0])).not.toContain('runtime');
    const extension = graph.extensions?.[CASEFLOW_AGENT_CANVAS_EXTENSION] as Record<string, unknown>;
    expect(Object.keys(extension)).not.toContain('run_state');
    expect(Object.keys(extension)).not.toContain('runtime_overlay');
  });

  it('fails closed for an unsupported extension schema', () => {
    const graph = fixtureGraph();
    graph.extensions = {
      ...graph.extensions,
      [CASEFLOW_AGENT_CANVAS_EXTENSION]: { schema: 'ananta.caseflow.agent-canvas/v99' },
    };
    const projected = projectAgentCanvas(graph);

    expect(projected.ok).toBe(false);
    expect(projected.issues[0]?.code).toBe('agent_canvas_schema_unsupported');
  });

  it('fails closed before persisting an unknown icon or malformed inspector tab', () => {
    const graph = fixtureGraph();
    const unknownIcon = serializeAgentCanvasExtension(graph, {
      schema: CASEFLOW_AGENT_CANVAS_SCHEMA_V1,
      nodes: { lead: { icon: 'remote_injected_icon' as any } },
    });
    const malformedTab = serializeAgentCanvasExtension(graph, {
      schema: CASEFLOW_AGENT_CANVAS_SCHEMA_V1,
      nodes: {
        lead: {
          inspector_hints: { default_tab: ['runtime'] as any },
        },
      },
    });

    expect(unknownIcon.ok).toBe(false);
    expect(unknownIcon.issues[0]?.code).toBe('agent_canvas_node_invalid');
    expect(malformedTab.ok).toBe(false);
    expect(malformedTab.issues[0]?.code).toBe('agent_canvas_node_invalid');
  });

  it('never classifies another self-loop as a reverse direction', () => {
    const graph = fixtureGraph();
    graph.edges = [
      ...graph.edges,
      edge('critic-loop-secondary', 'critic', 'critic'),
    ];
    const projected = projectAgentCanvas(graph);
    if (!projected.ok) throw new Error(JSON.stringify(projected.issues));

    expect(projected.value.edges.find(edge => edge.edge_id === 'critic-loop')?.reverse_edge_ids)
      .toEqual([]);
    expect(projected.value.edges.find(edge => edge.edge_id === 'critic-loop-secondary')?.reverse_edge_ids)
      .toEqual([]);
  });
});

function fixtureGraph(): VpGraph {
  const steps = [
    step('lead', 'Lead', 'lead', 0, 0, { future_step: { exact: ['keep', 7] } }),
    step('builder', 'Builder', 'developer', 220, 0),
    step('critic', 'Critic', 'critic', 440, 0),
  ];
  const edges: VpEdge[] = [
    edge('lead-builder', 'lead', 'builder', { future_edge: { correlation: 'canonical-edge-id' } }),
    edge('builder-lead', 'builder', 'lead'),
    edge('builder-critic', 'builder', 'critic'),
    {
      id: 'critic-loop',
      source: 'critic',
      target: 'critic',
      label: 'retry',
      condition: {
        kind: 'back_edge',
        loop_policy: { kind: 'fixed', max_iterations: 2 },
      },
    },
  ];
  return {
    id: 'graph-collaboration',
    name: 'Collaboration',
    description: 'Agent graph fixture',
    version: '1',
    tags: ['fixture'],
    steps,
    edges,
    metadata: { future_graph_metadata: { retained: true } },
    extensions: {
      [CASEFLOW_AGENT_CANVAS_EXTENSION]: {
        schema: CASEFLOW_AGENT_CANVAS_SCHEMA_V1,
        future_contract: { sequence: [3, 1, 4], unicode: 'unverändert' },
        nodes: {
          lead: {
            icon: 'star',
            future_node_field: { ordered: ['a', 'b'], exact: 1.25 },
          },
        },
      },
      'vendor.unrelated': { opaque: '{"whitespace": "is data"}' },
    },
  };
}

function step(
  id: string,
  label: string,
  role: string,
  x: number,
  y: number,
  extra: Record<string, unknown> = {},
): VpStep {
  return {
    id,
    label,
    role,
    kind: 'coding',
    agent_skill_profile_id: 'coder',
    io: { inputs: [], outputs: [] },
    position: { x, y },
    policy_hints: ['read_only'],
    gate: false,
    metadata: { future_metadata: { keep: true } },
    ...extra,
  } as VpStep;
}

function edge(
  id: string,
  source: string,
  target: string,
  extra: Record<string, unknown> = {},
): VpEdge {
  return {
    id,
    source,
    target,
    condition: { kind: 'always' },
    ...extra,
  } as VpEdge;
}
