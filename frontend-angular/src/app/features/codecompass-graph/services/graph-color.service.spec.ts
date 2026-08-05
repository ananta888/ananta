import { GraphEdge, GraphNode } from '../models/graph.model';
import {
  DEFAULT_GRAPH_VISUAL_PROFILE,
  GraphVisualProfile,
} from '../models/graph-visual-profile.model';
import { GraphColorService } from './graph-color.service';

function node(overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id: 'node',
    kind: 'python_file',
    rawNodeType: 'python_file',
    knownKind: 'python_file',
    label: 'node',
    file: '',
    content: '',
    recordId: '',
    metadata: {},
    ...overrides,
  };
}

describe('GraphColorService', () => {
  it('generates 30 valid, distinct colors without ten-item repetition', () => {
    const service = new GraphColorService();
    const colors = Array.from({ length: 30 }, (_, index) =>
      service.automaticDomainColor(`domain-${index}`));

    expect(colors.every(color => /^#[0-9A-F]{6}$/.test(color))).toBe(true);
    expect(new Set(colors).size).toBe(30);
    expect(colors.slice(0, 10)).not.toEqual(colors.slice(10, 20));
  });

  it('is stable across service instances, ordering and filtering', () => {
    const first = new GraphColorService();
    const second = new GraphColorService();
    const ids = ['orders', 'billing', 'catalog', 'shipping'];
    const forward = Object.fromEntries(ids.map(id => [id, first.automaticDomainColor(id)]));
    const reverse = Object.fromEntries([...ids].reverse().map(id => [id, second.automaticDomainColor(id)]));

    expect(reverse).toEqual(forward);
    expect(second.automaticDomainColor('orders')).toBe(forward['orders']);
    expect(second.marker('orders')).toBe(first.marker('orders'));
  });

  it('uses canonical identity precedence domain ID, domain path, file and unassigned', () => {
    const service = new GraphColorService();
    expect(service.resolveCanonicalDomain(node({
      domainId: 'orders',
      domainPath: 'fallback.path',
      file: 'fallback/file.py',
    }))).toMatchObject({ canonicalId: 'orders', source: 'domain_id' });
    expect(service.resolveCanonicalDomain(node({ domainPath: 'agent.routes', file: 'fallback.py' })))
      .toMatchObject({ canonicalId: 'agent.routes', source: 'domain_path' });
    expect(service.resolveCanonicalDomain(node({ file: './agent/routes.py' })))
      .toMatchObject({ canonicalId: 'file:agent/routes.py', source: 'file' });
    expect(service.resolveCanonicalDomain(node())).toMatchObject({ canonicalId: 'unassigned', source: 'unassigned' });
  });

  it('applies a safe manual domain override only to its canonical domain', () => {
    const service = new GraphColorService();
    const profile = {
      ...DEFAULT_GRAPH_VISUAL_PROFILE,
      domainColorOverrides: { orders: '#123456' },
    } as GraphVisualProfile;

    expect(service.nodeVisual(node({ domainId: 'orders' }), profile).color).toBe('#123456');
    expect(service.nodeVisual(node({ domainId: 'billing' }), profile).color).not.toBe('#123456');
  });

  it('uses kind and neutral fallbacks while preserving raw labels and non-color markers', () => {
    const service = new GraphColorService();
    const known = service.nodeVisual(node(), DEFAULT_GRAPH_VISUAL_PROFILE);
    const unknown = service.nodeVisual(node({
      kind: 'unknown',
      rawNodeType: 'custom_runtime_symbol',
      knownKind: null,
    }), DEFAULT_GRAPH_VISUAL_PROFILE);

    expect(known.color).toMatch(/^#[0-9A-F]{6}$/);
    expect(unknown.color).toBe('#64748B');
    expect(unknown.label).toBe('custom runtime symbol');
    expect(unknown.marker).toBeTruthy();
  });

  it('keeps unknown raw relations visible with text, marker and neutral fallback', () => {
    const service = new GraphColorService();
    const edge: GraphEdge = {
      id: 'edge',
      source: 'a',
      target: 'b',
      edgeType: 'related',
      rawEdgeType: 'custom_bus_link',
      knownRelation: null,
      confidence: 0,
      metadata: {},
    };

    const visual = service.edgeVisual(edge, DEFAULT_GRAPH_VISUAL_PROFILE);

    expect(visual.rawEdgeType).toBe('custom_bus_link');
    expect(visual.label).toBe('custom bus link');
    expect(visual.semanticallyKnown).toBe(false);
    expect(visual.color).toMatch(/^#[0-9a-fA-F]{6}$/);
    expect(visual.marker).toBeTruthy();
  });

  it('assigns distinct repository-structure colors when no domain identity exists', () => {
    const service = new GraphColorService();
    const repository = service.nodeVisual(node({
      kind: 'repository',
      rawNodeType: 'repository',
      knownKind: 'repository',
    }), DEFAULT_GRAPH_VISUAL_PROFILE);
    const directory = service.nodeVisual(node({
      kind: 'directory',
      rawNodeType: 'directory',
      knownKind: 'directory',
    }), DEFAULT_GRAPH_VISUAL_PROFILE);

    expect(repository.color).not.toBe('#64748B');
    expect(directory.color).not.toBe('#64748B');
    expect(repository.color).not.toBe(directory.color);
  });

  it('assigns canonical semantic nodes a non-neutral kind color', () => {
    const service = new GraphColorService();
    const semantic = service.nodeVisual(node({
      kind: 'semantic_node',
      rawNodeType: 'semantic_node',
      knownKind: 'semantic_node',
    }), DEFAULT_GRAPH_VISUAL_PROFILE);

    expect(semantic.color).not.toBe('#64748B');
    expect(semantic.label).toBe('semantic node');
  });
});
