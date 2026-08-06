import { HttpClient } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { SourceControlV1ApiClient } from '../../../services/source-control-v1-api.client';
import { InternalsService } from './internals.service';


describe('InternalsService CodeCompass graph window', () => {
  const graph = {
    schema: 'domain_graph_artifact.v1',
    source_kind: 'codecompass_graph',
    source_ref: 'index-example',
    nodes: [],
    edges: [],
    metadata: { node_count: 0, edge_count: 0 },
    warnings: [],
    text_alternative: 'Empty graph.',
    artifact_status: { state: 'available' },
  };
  const sourceControlApi = {
    loadGraph: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    sourceControlApi.loadGraph.mockReturnValue(of(graph));
    TestBed.configureTestingModule({
      providers: [
        InternalsService,
        { provide: HttpClient, useValue: {} },
        { provide: AgentDirectoryService, useValue: { list: () => [] } },
        { provide: SourceControlV1ApiClient, useValue: sourceControlApi },
      ],
    });
  });

  it('starts with a small topology-preserving project graph window', async () => {
    const service = TestBed.inject(InternalsService);

    const result = await firstValueFrom(
      service.getCodeCompassGraph('connection-example'),
    );

    expect(sourceControlApi.loadGraph).toHaveBeenCalledWith(
      'connection-example',
      { limit: 100, view: 'topology', maxEdges: 400 },
    );
    expect(result).toBe(graph);
  });

  it('requests an explicit domain window including its subdomains', async () => {
    const service = TestBed.inject(InternalsService);

    const result = await firstValueFrom(
      service.getCodeCompassGraph('connection-example', {
        limit: 75,
        maxEdges: 300,
        domainScope: 'domain:frontend',
        includeSubdomains: true,
      }),
    );

    expect(sourceControlApi.loadGraph).toHaveBeenCalledWith(
      'connection-example',
      {
        limit: 75,
        view: 'topology',
        maxEdges: 300,
        domainScope: 'domain:frontend',
        includeSubdomains: true,
      },
    );
    expect(result).toBe(graph);
  });

  it('maps a valid paged domain inventory without coercing contract fields', async () => {
    sourceControlApi.loadGraph.mockReturnValueOnce(of({
      schema: 'codecompass_graph_inventory.v1',
      facets: {
        domains: {
          items: [
            {
              key: 'domain:frontend',
              label: ' Frontend ',
              parent_key: null,
              depth: 0,
              direct_node_count: 12,
              subtree_node_count: 30,
              has_children: true,
              source: ' explicit ',
              path: ' src/app ',
            },
            {
              key: 'domain:frontend/components',
              label: ' Components ',
              parent_key: ' domain:frontend ',
              depth: 1,
              direct_node_count: 4,
              subtree_node_count: 4,
              has_children: false,
              source: 'explicit',
              path: 'src/app/components',
            },
          ],
          next_cursor: ' inventory:2 ',
          total_count: 2,
        },
      },
      metadata: {
        total_nodes: 42,
        total_edges: 64,
      },
      graph_revision: ' revision-example ',
    }));
    const service = TestBed.inject(InternalsService);

    const result = await firstValueFrom(
      service.getCodeCompassGraphInventory(
        'connection-example',
        'inventory:0',
        50,
      ),
    );

    expect(sourceControlApi.loadGraph).toHaveBeenCalledWith(
      'connection-example',
      {
        cursor: 'inventory:0',
        limit: 50,
        view: 'inventory',
      },
    );
    expect(result).toEqual({
      domains: [
        {
          key: 'domain:frontend',
          label: 'Frontend',
          parentKey: null,
          depth: 0,
          directNodeCount: 12,
          subtreeNodeCount: 30,
          hasChildren: true,
          source: 'explicit',
          path: 'src/app',
        },
        {
          key: 'domain:frontend/components',
          label: 'Components',
          parentKey: 'domain:frontend',
          depth: 1,
          directNodeCount: 4,
          subtreeNodeCount: 4,
          hasChildren: false,
          source: 'explicit',
          path: 'src/app/components',
        },
      ],
      nextCursor: 'inventory:2',
      totalDomains: 2,
      totalNodes: 42,
      totalEdges: 64,
      graphRevision: 'revision-example',
    });
  });

  it.each([
    ['numeric strings', { direct_node_count: '12' }],
    ['negative counts', { direct_node_count: -1 }],
    ['fractional counts', { depth: 1.5 }],
    ['non-boolean child evidence', { has_children: 'true' }],
  ])('rejects malformed inventory %s instead of silently defaulting it', async (_name, patch) => {
    sourceControlApi.loadGraph.mockReturnValueOnce(of({
      schema: 'codecompass_graph_inventory.v1',
      facets: {
        domains: {
          items: [{
            key: 'domain:frontend',
            label: 'Frontend',
            parent_key: null,
            depth: 0,
            direct_node_count: 12,
            subtree_node_count: 30,
            has_children: true,
            source: 'explicit',
            path: 'src/app',
            ...patch,
          }],
          next_cursor: null,
          total_count: 1,
        },
      },
      metadata: { total_nodes: 42, total_edges: 64 },
      graph_revision: 'revision-example',
    }));
    const service = TestBed.inject(InternalsService);

    await expect(firstValueFrom(
      service.getCodeCompassGraphInventory('connection-example'),
    )).rejects.toThrow(/graph_inventory/);
  });

  it('accepts a structurally valid empty inventory', async () => {
    sourceControlApi.loadGraph.mockReturnValueOnce(of({
      schema: 'codecompass_graph_inventory.v1',
      facets: {
        domains: { items: [], next_cursor: null, total_count: 0 },
      },
      metadata: { total_nodes: 0, total_edges: 0 },
      graph_revision: 'revision-empty',
    }));
    const service = TestBed.inject(InternalsService);

    await expect(firstValueFrom(
      service.getCodeCompassGraphInventory('connection-example'),
    )).resolves.toEqual({
      domains: [],
      nextCursor: null,
      totalDomains: 0,
      totalNodes: 0,
      totalEdges: 0,
      graphRevision: 'revision-empty',
    });
  });

  it('maps a scoped staged edge transport page without treating its chunk size as a graph cap', async () => {
    sourceControlApi.loadGraph.mockReturnValueOnce(of({
      ...graph,
      nodes: [],
      edges: [{ edge_id: 'edge-1', source_id: 'node-1', target_id: 'node-2' }],
      metadata: {
        view: 'staged',
        stage: 'edges',
        next_cursor: 'edge:2000',
        delivery_returned: 1,
        delivery_total: 2_136,
        delivery_complete: false,
        content_graph_revision: 'revision-example',
      },
    }));
    const service = TestBed.inject(InternalsService);

    const result = await firstValueFrom(service.getCodeCompassGraphStagedPage(
      'connection-example',
      {
        stage: 'edges',
        cursor: 'edge:0',
        pageSize: 2_000,
        domainScope: 'domain:agent/codecompass',
        includeSubdomains: true,
      },
    ));

    expect(sourceControlApi.loadGraph).toHaveBeenCalledWith('connection-example', {
      view: 'staged',
      stage: 'edges',
      cursor: 'edge:0',
      limit: 500,
      maxEdges: 2_000,
      domainScope: 'domain:agent/codecompass',
      includeSubdomains: true,
    });
    expect(result).toMatchObject({
      stage: 'edges',
      nextCursor: 'edge:2000',
      returned: 1,
      total: 2_136,
      graphRevision: 'revision-example',
      complete: false,
    });
  });

  it.each([
    ['stage-foreign nodes', {
      nodes: [{ node_id: 'unexpected' }],
      edges: [{ edge_id: 'edge-1', source_id: 'a', target_id: 'b' }],
    }],
    ['missing edge id', {
      nodes: [],
      edges: [{ source_id: 'a', target_id: 'b' }],
    }],
    ['legacy edge id only', {
      nodes: [],
      edges: [{ id: 'legacy-edge', source_id: 'a', target_id: 'b' }],
    }],
    ['numeric edge id', {
      nodes: [],
      edges: [{ edge_id: 1, source_id: 'a', target_id: 'b' }],
    }],
    ['missing edge endpoint', {
      nodes: [],
      edges: [{ edge_id: 'edge-1', source_id: 'a' }],
    }],
  ])('rejects staged edge pages with %s', async (_name, records) => {
    sourceControlApi.loadGraph.mockReturnValueOnce(of({
      ...graph,
      ...records,
      metadata: {
        view: 'staged',
        stage: 'edges',
        next_cursor: null,
        delivery_returned: records.edges.length,
        delivery_total: records.edges.length,
        delivery_complete: true,
        content_graph_revision: 'revision-example',
      },
    }));
    const service = TestBed.inject(InternalsService);

    await expect(firstValueFrom(service.getCodeCompassGraphStagedPage(
      'connection-example',
      { stage: 'edges', pageSize: 2_000 },
    ))).rejects.toThrow(/graph_staged/);
  });

  it.each([
    ['legacy id only', { id: 'legacy-node' }],
    ['numeric node id', { node_id: 1 }],
  ])('rejects staged node pages with %s', async (_name, node) => {
    sourceControlApi.loadGraph.mockReturnValueOnce(of({
      ...graph,
      nodes: [node],
      edges: [],
      metadata: {
        view: 'staged',
        stage: 'nodes',
        next_cursor: null,
        delivery_returned: 1,
        delivery_total: 1,
        delivery_complete: true,
        content_graph_revision: 'revision-example',
      },
    }));
    const service = TestBed.inject(InternalsService);

    await expect(firstValueFrom(service.getCodeCompassGraphStagedPage(
      'connection-example',
      { stage: 'nodes', pageSize: 500 },
    ))).rejects.toThrow(/graph_staged/);
  });
});
