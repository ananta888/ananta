import { describe, expect, it } from 'vitest';

import { ChTopologyReadModel } from '../models/codehug.models';
import { buildTopologyGraph } from './codehug-topology-layout';

describe('buildTopologyGraph', () => {
  it('places hubs, workers, layers and rules deterministically', () => {
    const topology: ChTopologyReadModel = {
      hubs: [{ id: 'hub-1', url: 'http://hub', status: 'online', version: '1', startedAt: 1 }],
      workers: [{
        id: 'worker-1',
        hubId: 'hub-1',
        type: 'ananta-worker',
        cliBackend: 'codex',
        model: 'gpt-test',
        llmProvider: 'openai',
        capabilities: ['coding'],
        health: 'healthy',
        boundary: 'local-only',
        registeredAt: 1,
        lastHeartbeatAt: 1,
      }],
      connections: [],
      activeLayers: [{ id: 'layer-1', name: 'Tests', order: 1, enabled: true, parameters: {} }],
      routingRules: [{
        id: 'rule-1',
        description: 'Coding',
        match: {},
        selectedBackend: 'codex',
        selectedModel: 'gpt-test',
        priority: 1,
      }],
    };

    const first = buildTopologyGraph(topology);
    const second = buildTopologyGraph(topology);

    expect(second).toEqual(first);
    expect(first.nodes.map(node => node.id)).toEqual([
      'hub::hub-1',
      'worker::worker-1',
      'layer::layer-1',
      'rule::rule-1',
    ]);
    expect(first.edges).toContainEqual({
      id: 'e-hub-1-worker-1',
      fromId: 'hub::hub-1',
      toId: 'worker::worker-1',
      runState: 'idle',
    });
  });
});
