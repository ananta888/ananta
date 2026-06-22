import { TestBed } from '@angular/core/testing';
import { GraphAdapterService } from './graph-adapter.service';
import { MOCK_DOMAIN_GRAPH_ARTIFACT } from '../testing/mock-codecompass-graph';

describe('GraphAdapterService', () => {
  let svc: GraphAdapterService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    svc = TestBed.inject(GraphAdapterService);
  });

  it('maps mock artifact to GenericGraphModel with correct counts', () => {
    const model = svc.fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
    expect(model.nodes.length).toBe(20);
    expect(model.edges.length).toBe(30);
  });

  it('maps node_id to id and node_type to kind', () => {
    const model = svc.fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
    const os = model.nodes.find(n => n.id === 'n-OrderService')!;
    expect(os).toBeTruthy();
    expect(os.kind).toBe('java_type');
    expect(os.label).toBe('OrderService');
    expect(os.file).toBe('src/main/java/shop/OrderService.java');
  });

  it('maps edge relation to edgeType and confidence from attributes', () => {
    const model = svc.fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
    const e = model.edges.find(e => e.source === 'n-OrderService' && e.edgeType === 'extends')!;
    expect(e).toBeTruthy();
    expect(e.target).toBe('n-BaseService');
    expect(e.confidence).toBe(1.0);
  });

  it('maps unknown node_type to "unknown"', () => {
    const raw = {
      nodes: [{ node_id: 'x1', node_type: 'not_a_real_type', attributes: { name: 'X' } }],
      edges: [],
    };
    const model = svc.fromDomainArtifact(raw);
    expect(model.nodes[0].kind).toBe('unknown');
  });

  it('maps unknown relation to "related"', () => {
    const raw = {
      nodes: [
        { node_id: 'a', node_type: 'java_type', attributes: { name: 'A' } },
        { node_id: 'b', node_type: 'java_type', attributes: { name: 'B' } },
      ],
      edges: [{ source_id: 'a', target_id: 'b', relation: 'fantasy_relation', attributes: {} }],
    };
    const model = svc.fromDomainArtifact(raw);
    expect(model.edges[0].edgeType).toBe('related');
  });

  it('returns empty model for null input', () => {
    const model = svc.fromDomainArtifact(null);
    expect(model.nodes).toEqual([]);
    expect(model.edges).toEqual([]);
  });

  it('returns empty model for empty nodes/edges', () => {
    const model = svc.fromDomainArtifact({ nodes: [], edges: [] });
    expect(model.nodes.length).toBe(0);
    expect(model.edges.length).toBe(0);
  });

  it('skips edges with missing source or target', () => {
    const raw = {
      nodes: [],
      edges: [
        { source_id: '', target_id: 'b', relation: 'related', attributes: {} },
        { source_id: 'a', target_id: '', relation: 'related', attributes: {} },
      ],
    };
    const model = svc.fromDomainArtifact(raw);
    expect(model.edges.length).toBe(0);
  });

  it('skips nodes with empty node_id', () => {
    const raw = {
      nodes: [{ node_id: '', node_type: 'java_type', attributes: { name: 'X' } }],
      edges: [],
    };
    const model = svc.fromDomainArtifact(raw);
    expect(model.nodes.length).toBe(0);
  });

  it('propagates warnings from artifact', () => {
    const model = svc.fromDomainArtifact({ nodes: [], edges: [], warnings: ['degraded'] });
    expect(model.warnings).toContain('degraded');
  });

  it('derives edge id from source|target|relation', () => {
    const raw = {
      nodes: [],
      edges: [{ source_id: 'a', target_id: 'b', relation: 'extends', attributes: {} }],
    };
    const model = svc.fromDomainArtifact(raw);
    expect(model.edges[0].id).toBe('a|b|extends');
  });

  it('keeps optional domain hierarchy metadata on nodes', () => {
    const raw = {
      nodes: [{
        node_id: 'a',
        node_type: 'python_file',
        attributes: {
          name: 'pair_groups.py',
          file: 'agent/routes/pair_groups.py',
          domain_path: 'agent.routes.pair_groups',
          domain_level: 1,
        },
      }],
      edges: [],
    };

    const model = svc.fromDomainArtifact(raw);

    expect(model.nodes[0].metadata['domain_path']).toBe('agent.routes.pair_groups');
    expect(model.nodes[0].metadata['domain_level']).toBe(1);
  });
});
