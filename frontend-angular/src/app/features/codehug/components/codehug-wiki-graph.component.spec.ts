import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { Observable, Subject, of, throwError } from 'rxjs';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { CodehugWikiGraphComponent } from './codehug-wiki-graph.component';
import {
  CodeCompassGraphInventoryPage,
  CodeCompassGraphStagedPage,
  InternalsService,
} from '../services/internals.service';


beforeAll(async () => {
  await ɵresolveComponentResources((resource) =>
    readFile(new URL(String(resource), import.meta.url), 'utf8'),
  );
});


function inventoryPage(
  overrides: Partial<CodeCompassGraphInventoryPage> = {},
): CodeCompassGraphInventoryPage {
  return {
    domains: [],
    nextCursor: null,
    totalDomains: 0,
    totalNodes: 0,
    totalEdges: 0,
    graphRevision: 'revision-example',
    ...overrides,
  };
}


function emptyGraph(): Record<string, unknown> {
  return {
    metadata: { node_count: 0, edge_count: 0 },
    nodes: [],
    edges: [],
  };
}


function codeGraph(
  revision = 'revision-example',
  nodeCount = 1,
): Record<string, any> {
  return {
    metadata: {
      node_count: nodeCount,
      edge_count: 0,
      scope_total_nodes: Math.max(nodeCount, 350),
      global_total_nodes: Math.max(nodeCount, 800),
      global_total_edges: 0,
      evidence_graph_revision: revision,
      graph_revision: `projection-${revision}-${nodeCount}`,
      window_node_limit: nodeCount > 100 ? nodeCount : 100,
    },
    nodes: Array.from({ length: nodeCount }, (_, index) => ({ node_id: `node-${index}` })),
    edges: [],
  };
}

function stagedPage(
  stage: 'nodes' | 'edges',
  records: Array<Record<string, unknown>>,
  overrides: Partial<{
    nextCursor: string | null;
    total: number;
    revision: string;
    totalNodes: number;
    totalEdges: number;
    domainScope: string | null;
    includeSubdomains: boolean;
  }> = {},
): CodeCompassGraphStagedPage {
  const nextCursor = overrides.nextCursor ?? null;
  const total = overrides.total ?? records.length;
  const revision = overrides.revision ?? 'revision-example';
  const graph = {
    schema: 'codecompass_graph.v1',
    knowledge_index_id: 'index-example',
    source_ref: 'index-example',
    nodes: stage === 'nodes' ? records : [],
    edges: stage === 'edges' ? records : [],
    metadata: {
      view: 'staged',
      stage,
      next_cursor: nextCursor,
      content_graph_revision: revision,
      delivery_returned: records.length,
      delivery_total: total,
      delivery_complete: nextCursor === null,
      knowledge_index_id: 'index-example',
      domain_scope: overrides.domainScope ?? null,
      include_subdomains: overrides.includeSubdomains ?? true,
      total_nodes: overrides.totalNodes ?? (stage === 'nodes' ? total : 1),
      total_edges: overrides.totalEdges ?? (stage === 'edges' ? total : 0),
      global_total_nodes: 800,
      global_source_edge_count: 1_200,
    },
    diagnostics: {},
    warnings: [],
  };
  return {
    graph,
    stage,
    nextCursor,
    returned: records.length,
    total,
    graphRevision: revision,
    complete: nextCursor === null,
  };
}


describe('CodehugWikiGraphComponent', () => {
  const service = {
    listKnowledgeIndexes: vi.fn(),
    getCodeCompassGraph: vi.fn(),
    getCodeCompassGraphStagedPage: vi.fn(),
    getCodeCompassGraphInventory: vi.fn(),
    getWikiGraphStatus: vi.fn(() => of(null)),
    getWikiDomainStatus: vi.fn(() => of(null)),
    getWikiDomains: vi.fn(() => of([])),
    searchWikiArticles: vi.fn(() => of([])),
    expandWikiArticle: vi.fn(() => of(null)),
    getWikiDomainGraph: vi.fn(() => of(null)),
    triggerWikiGraphBuild: vi.fn(() => of({})),
    buildWikiDomains: vi.fn(() => of({})),
  };

  beforeEach(async () => {
    vi.clearAllMocks();
    service.getCodeCompassGraph.mockReturnValue(of(emptyGraph()));
    service.getCodeCompassGraphStagedPage.mockImplementation(
      (_connectionId: string, request: {
        stage: 'nodes' | 'edges';
        domainScope?: string;
        includeSubdomains: boolean;
      }) => of(
        stagedPage(
          request.stage,
          request.stage === 'nodes' ? [{ node_id: 'domain-node' }] : [],
          {
            domainScope: request.domainScope ?? null,
            includeSubdomains: request.includeSubdomains,
          },
        ),
      ),
    );
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage()));
    service.getWikiGraphStatus.mockReturnValue(of(null));
    service.getWikiDomainStatus.mockReturnValue(of(null));
    service.getWikiDomains.mockReturnValue(of([]));
    service.triggerWikiGraphBuild.mockReturnValue(of({}));
    service.buildWikiDomains.mockReturnValue(of({}));
    await TestBed.configureTestingModule({
      imports: [CodehugWikiGraphComponent],
      providers: [{ provide: InternalsService, useValue: service }],
    }).compileComponents();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('loads the first active project connection without an unbound self graph', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
      source_scope: 'connection',
    }]));

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.selectedConnectionId()).toBe('conn-example');
    expect(service.getCodeCompassGraphInventory).toHaveBeenCalledWith(
      'conn-example',
      undefined,
    );
    expect(service.getCodeCompassGraph).toHaveBeenCalledWith(
      'conn-example',
      {
        limit: 100,
        maxEdges: 400,
        includeSubdomains: true,
      },
    );
    expect(fixture.componentInstance.graphLoadStrategy()).toBe('fast');
    expect(fixture.componentInstance.requestedNodeLimit()).toBe(100);
    expect(
      service.getCodeCompassGraph.mock.invocationCallOrder[0],
    ).toBeLessThan(service.getCodeCompassGraphInventory.mock.invocationCallOrder[0]!);
    expect(fixture.componentInstance.error()).not.toContain('ungebundene Self-Graph');
  });

  it('explains that a graph-capable profile is required for an empty index', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.error()).toContain('Deep Code');
  });

  it('reports a missing project index without requesting an unbound graph', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([]));

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(service.getCodeCompassGraph).not.toHaveBeenCalled();
    expect(fixture.componentInstance.error()).toContain('aktivem Index');
  });

  it('contains project-index loading failures', () => {
    service.listKnowledgeIndexes.mockReturnValue(
      throwError(() => new Error('unavailable')),
    );

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.loading()).toBe(false);
    expect(fixture.componentInstance.error()).toContain('nicht geladen');
  });

  it('clears stale graph metadata when the graph view is cleared', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([]));
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    fixture.componentInstance.rawGraph.set({ nodes: [{ node_id: 'old' }] });
    fixture.componentInstance.metadata.set({ node_count: 1, edge_count: 0 });

    fixture.componentInstance.search('');

    expect(fixture.componentInstance.rawGraph()).toBeNull();
    expect(fixture.componentInstance.metadata()).toBeNull();
  });

  it('cancels graph and inventory requests before loading another source', () => {
    const graphTeardown = vi.fn();
    const inventoryTeardown = vi.fn();
    service.listKnowledgeIndexes.mockReturnValue(of([
      { id: 'conn-old', knowledge_index_id: 'index-old' },
      { id: 'conn-new', knowledge_index_id: 'index-new' },
    ]));
    service.getCodeCompassGraph.mockReturnValueOnce(
      new Observable(subscriber => {
        subscriber.next(emptyGraph());
        return graphTeardown;
      }),
    );
    service.getCodeCompassGraphInventory.mockReturnValueOnce(
      new Observable(() => inventoryTeardown),
    );

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    fixture.componentInstance.changeSource('conn-new');

    expect(graphTeardown).toHaveBeenCalledTimes(1);
    expect(inventoryTeardown).toHaveBeenCalledTimes(1);
    expect(service.getCodeCompassGraphInventory).toHaveBeenNthCalledWith(
      2,
      'conn-new',
      undefined,
    );
    expect(service.getCodeCompassGraph).toHaveBeenNthCalledWith(
      2,
      'conn-new',
      {
        limit: 100,
        maxEdges: 400,
        includeSubdomains: true,
      },
    );
  });

  it('cancels an automatically requested next inventory page when the connection changes', () => {
    const staleNextPage = new Subject<CodeCompassGraphInventoryPage>();
    service.listKnowledgeIndexes.mockReturnValue(of([
      { id: 'conn-old', knowledge_index_id: 'index-old' },
      { id: 'conn-new', knowledge_index_id: 'index-new' },
    ]));
    service.getCodeCompassGraph.mockImplementation((connectionId: string) => of(
      codeGraph(connectionId === 'conn-old' ? 'revision-old' : 'revision-new'),
    ));
    service.getCodeCompassGraphInventory
      .mockReturnValueOnce(of(inventoryPage({
        domains: [{
          key: 'domain:old',
          label: 'Old',
          parentKey: null,
          depth: 0,
          directNodeCount: 1,
          subtreeNodeCount: 1,
          hasChildren: false,
          source: 'domain_id',
          path: 'old',
        }],
        nextCursor: 'inventory:old:1',
        totalDomains: 2,
        graphRevision: 'revision-old',
      })))
      .mockReturnValueOnce(staleNextPage)
      .mockReturnValueOnce(of(inventoryPage({
        domains: [{
          key: 'domain:new',
          label: 'New',
          parentKey: null,
          depth: 0,
          directNodeCount: 1,
          subtreeNodeCount: 1,
          hasChildren: false,
          source: 'domain_id',
          path: 'new',
        }],
        totalDomains: 1,
        graphRevision: 'revision-new',
      })));

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    expect(staleNextPage.observed).toBe(true);

    fixture.componentInstance.changeSource('conn-new');
    staleNextPage.next(inventoryPage({
      totalDomains: 2,
      graphRevision: 'revision-old',
    }));

    expect(staleNextPage.observed).toBe(false);
    expect(service.getCodeCompassGraphInventory).toHaveBeenNthCalledWith(
      2,
      'conn-old',
      'inventory:old:1',
    );
    expect(service.getCodeCompassGraphInventory).toHaveBeenNthCalledWith(
      3,
      'conn-new',
      undefined,
    );
    expect(fixture.componentInstance.graphDomains().map(domain => domain.key)).toEqual([
      'domain:new',
    ]);
    expect(fixture.componentInstance.graphInventoryRevision()).toBe('revision-new');
  });

  it('loads every domain page automatically and merges facets deterministically by key', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraphInventory
      .mockReturnValueOnce(of(inventoryPage({
        domains: [{
          key: 'domain:frontend',
          label: 'Frontend',
          parentKey: null,
          depth: 0,
          directNodeCount: 4,
          subtreeNodeCount: 10,
          hasChildren: true,
          source: 'domain_id',
          path: 'frontend',
        }],
        nextCursor: 'inventory:1',
        totalDomains: 2,
        totalNodes: 30,
        totalEdges: 50,
      })))
      .mockReturnValueOnce(of(inventoryPage({
        domains: [
          {
            key: 'domain:frontend',
            label: 'Frontend',
            parentKey: null,
            depth: 0,
            directNodeCount: 5,
            subtreeNodeCount: 12,
            hasChildren: true,
            source: 'domain_id',
            path: 'frontend',
          },
          {
            key: 'domain:frontend/components',
            label: 'Components',
            parentKey: 'domain:frontend',
            depth: 1,
            directNodeCount: 7,
            subtreeNodeCount: 7,
            hasChildren: false,
            source: 'domain_id',
            path: 'frontend/components',
          },
        ],
        nextCursor: null,
        totalDomains: 2,
        totalNodes: 30,
        totalEdges: 50,
      })));

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(service.getCodeCompassGraphInventory).toHaveBeenNthCalledWith(
      2,
      'conn-example',
      'inventory:1',
    );
    expect(fixture.componentInstance.graphDomains().map(domain => domain.key)).toEqual([
      'domain:frontend',
      'domain:frontend/components',
    ]);
    expect(fixture.componentInstance.graphDomains()[0]?.subtreeNodeCount).toBe(12);
    expect(fixture.componentInstance.graphDomainTotal()).toBe(2);
    expect(fixture.componentInstance.graphInventoryNodeTotal()).toBe(30);
    expect(fixture.componentInstance.graphInventoryEdgeTotal()).toBe(50);
    expect(fixture.componentInstance.graphDomainLoading()).toBe(false);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain(
      'Domain-Inventar: 2 / 2 Bereiche geladen',
    );
  });

  it('stops automatic inventory paging when the server repeats a cursor', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraphInventory
      .mockReturnValueOnce(of(inventoryPage({
        domains: [{
          key: 'domain:frontend',
          label: 'Frontend',
          parentKey: null,
          depth: 0,
          directNodeCount: 4,
          subtreeNodeCount: 4,
          hasChildren: false,
          source: 'domain_id',
          path: 'frontend',
        }],
        nextCursor: 'inventory:repeated',
        totalDomains: 3,
      })))
      .mockReturnValueOnce(of(inventoryPage({
        domains: [{
          key: 'domain:backend',
          label: 'Backend',
          parentKey: null,
          depth: 0,
          directNodeCount: 5,
          subtreeNodeCount: 5,
          hasChildren: false,
          source: 'domain_id',
          path: 'backend',
        }],
        nextCursor: 'inventory:repeated',
        totalDomains: 3,
      })))
      .mockReturnValueOnce(of(inventoryPage({
        domains: [
          {
            key: 'domain:frontend',
            label: 'Frontend',
            parentKey: null,
            depth: 0,
            directNodeCount: 4,
            subtreeNodeCount: 4,
            hasChildren: false,
            source: 'domain_id',
            path: 'frontend',
          },
          {
            key: 'domain:backend',
            label: 'Backend',
            parentKey: null,
            depth: 0,
            directNodeCount: 5,
            subtreeNodeCount: 5,
            hasChildren: false,
            source: 'domain_id',
            path: 'backend',
          },
        ],
        totalDomains: 2,
      })));

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(service.getCodeCompassGraphInventory).toHaveBeenCalledTimes(2);
    expect(fixture.componentInstance.graphDomains().map(domain => domain.key)).toEqual([
      'domain:frontend',
      'domain:backend',
    ]);
    expect(fixture.componentInstance.graphDomainLoading()).toBe(false);
    expect(fixture.componentInstance.graphDomainNextCursor()).toBeNull();
    expect(fixture.componentInstance.graphDomainError()).toContain(
      'bereits verwendeten Cursor wiederholt',
    );
    fixture.detectChanges();
    const retryButton = [...fixture.nativeElement.querySelectorAll('button')]
      .find((button: HTMLButtonElement) => button.textContent?.includes('Inventar erneut laden'));
    expect(retryButton).toBeDefined();

    retryButton!.click();

    expect(service.getCodeCompassGraphInventory).toHaveBeenCalledTimes(3);
    expect(fixture.componentInstance.graphDomainError()).toBe('');
    expect(fixture.componentInstance.graphDomainTotal()).toBe(2);
    expect(fixture.componentInstance.graphDomains()).toHaveLength(2);
  });

  it('loads a selected domain completely with and without its subdomains', () => {
    const frontendDomain = {
      key: 'domain:frontend',
      label: 'Frontend',
      parentKey: null,
      depth: 0,
      directNodeCount: 4,
      subtreeNodeCount: 18,
      hasChildren: true,
      source: 'domain_id',
      path: 'frontend',
    } as const;
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage({
      domains: [frontendDomain],
      totalDomains: 1,
      totalNodes: 20,
    })));

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    fixture.componentInstance.changeGraphDomain(frontendDomain.key);

    expect(service.getCodeCompassGraph).toHaveBeenCalledTimes(1);
    expect(service.getCodeCompassGraphStagedPage).toHaveBeenNthCalledWith(
      1,
      'conn-example',
      {
        stage: 'nodes',
        pageSize: 500,
        domainScope: 'domain:frontend',
        includeSubdomains: true,
      },
    );
    expect(service.getCodeCompassGraphStagedPage).toHaveBeenNthCalledWith(
      2,
      'conn-example',
      {
        stage: 'edges',
        pageSize: 2_000,
        domainScope: 'domain:frontend',
        includeSubdomains: true,
      },
    );
    expect(fixture.componentInstance.graphDomainOptionLabel(frontendDomain)).toBe(
      'frontend · deklarierte Domain (18)',
    );
    expect(fixture.componentInstance.fullGraphLoadState()).toBe('complete');
    expect(fixture.componentInstance.rawGraph()?.nodes).toEqual([
      { node_id: 'domain-node' },
    ]);

    fixture.componentInstance.changeGraphSubdomainPolicy(false);

    expect(service.getCodeCompassGraphStagedPage).toHaveBeenNthCalledWith(
      3,
      'conn-example',
      {
        stage: 'nodes',
        pageSize: 500,
        domainScope: 'domain:frontend',
        includeSubdomains: false,
      },
    );
    expect(service.getCodeCompassGraphStagedPage).toHaveBeenNthCalledWith(
      4,
      'conn-example',
      {
        stage: 'edges',
        pageSize: 2_000,
        domainScope: 'domain:frontend',
        includeSubdomains: false,
      },
    );
    expect(fixture.componentInstance.graphDomainOptionLabel(frontendDomain)).toBe(
      'frontend · deklarierte Domain (4)',
    );
  });

  it('loads the entire index only after an explicit request and can return to its preview', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraph.mockReturnValue(of(codeGraph()));
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage({
      totalNodes: 800,
    })));
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(service.getCodeCompassGraphStagedPage).not.toHaveBeenCalled();
    fixture.componentInstance.loadFullGraphScope();

    expect(service.getCodeCompassGraphStagedPage).toHaveBeenCalledTimes(2);
    expect(service.getCodeCompassGraphStagedPage.mock.calls.every(call => (
      call[1].domainScope === undefined
    ))).toBe(true);
    expect(fixture.componentInstance.fullGraphLoadState()).toBe('complete');

    fixture.componentInstance.returnToGraphPreview();

    expect(service.getCodeCompassGraph).toHaveBeenCalledTimes(2);
    expect(fixture.componentInstance.fullGraphLoadState()).toBe('idle');
  });

  it('cancels an in-flight domain stream on source change without accepting stale data', () => {
    const activeNodes = new Subject<CodeCompassGraphStagedPage>();
    service.listKnowledgeIndexes.mockReturnValue(of([
      { id: 'conn-old', knowledge_index_id: 'index-old' },
      { id: 'conn-new', knowledge_index_id: 'index-new' },
    ]));
    service.getCodeCompassGraph.mockImplementation((connectionId: string) => of(
      codeGraph(connectionId === 'conn-old' ? 'revision-old' : 'revision-new'),
    ));
    service.getCodeCompassGraphInventory.mockImplementation((connectionId: string) => of(
      inventoryPage({
        graphRevision: connectionId === 'conn-old' ? 'revision-old' : 'revision-new',
      }),
    ));
    service.getCodeCompassGraphStagedPage.mockReturnValueOnce(activeNodes);
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    fixture.componentInstance.changeGraphDomain('domain:agent');
    expect(activeNodes.observed).toBe(true);

    fixture.componentInstance.changeSource('conn-new');
    activeNodes.next(stagedPage('nodes', [{ node_id: 'stale' }], {
      revision: 'revision-old',
      domainScope: 'domain:agent',
    }));

    expect(activeNodes.observed).toBe(false);
    expect(fixture.componentInstance.selectedConnectionId()).toBe('conn-new');
    expect(fixture.componentInstance.rawGraph()?.nodes).not.toContainEqual({ node_id: 'stale' });
    expect(fixture.componentInstance.fullGraphLoadState()).toBe('idle');
  });

  it('offers a direct domain retry after cancellation', () => {
    const activeNodes = new Subject<CodeCompassGraphStagedPage>();
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraph.mockReturnValue(of(codeGraph('revision-old')));
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage({
      graphRevision: 'revision-old',
    })));
    service.getCodeCompassGraphStagedPage.mockReturnValueOnce(activeNodes);
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    fixture.componentInstance.changeGraphDomain('domain:agent');
    fixture.componentInstance.cancelFullGraphLoad();
    fixture.detectChanges();

    expect(fixture.componentInstance.fullGraphLoadState()).toBe('cancelled');
    expect(fixture.nativeElement.textContent).toContain('Domain vollständig erneut laden');
  });

  it('refreshes the bound index before retrying a failed complete load', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraph.mockReturnValue(of(codeGraph('revision-old')));
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage({
      graphRevision: 'revision-old',
    })));
    service.getCodeCompassGraphStagedPage.mockReturnValue(
      throwError(() => new Error('stale transport')),
    );
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    fixture.componentInstance.changeGraphDomain('domain:agent');
    fixture.detectChanges();

    expect(fixture.componentInstance.fullGraphLoadState()).toBe('error');
    const refreshButton = Array.from(
      fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>,
    ).find(button => button.textContent?.includes('Aktuellen Indexstand laden'));
    expect(refreshButton).toBeDefined();
    refreshButton?.click();

    expect(service.getCodeCompassGraph).toHaveBeenCalledTimes(2);
    expect(fixture.componentInstance.selectedGraphDomain()).toBe('');
    expect(fixture.componentInstance.fullGraphLoadState()).toBe('idle');
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Gesamten Index vollständig laden');
  });

  it('cancels a domain stream before entering the Wiki search context', () => {
    const activeNodes = new Subject<CodeCompassGraphStagedPage>();
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraph.mockReturnValue(of(codeGraph()));
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage()));
    service.getCodeCompassGraphStagedPage.mockReturnValueOnce(activeNodes);
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    fixture.componentInstance.changeGraphDomain('domain:agent');

    fixture.componentInstance.search('architecture');
    activeNodes.next(stagedPage('nodes', [{ node_id: 'stale' }], {
      domainScope: 'domain:agent',
    }));

    expect(activeNodes.observed).toBe(false);
    expect(fixture.componentInstance.fullGraphLoadState()).toBe('idle');
    expect(fixture.componentInstance.rawGraph()).toBeNull();
  });

  it('grows the fast graph window in bounded 100-node steps', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    fixture.componentInstance.rawGraph.set({
      nodes: Array.from({ length: 100 }, (_, index) => ({ id: `node-${index}` })),
      edges: [],
    });
    fixture.componentInstance.metadata.set({
      node_count: 100,
      scope_total_nodes: 350,
      global_total_nodes: 800,
    });

    expect(fixture.componentInstance.canGrowGraphWindow()).toBe(true);
    fixture.componentInstance.growGraphWindow();

    expect(fixture.componentInstance.requestedNodeLimit()).toBe(200);
    expect(service.getCodeCompassGraph).toHaveBeenNthCalledWith(
      2,
      'conn-example',
      {
        limit: 200,
        maxEdges: 800,
        includeSubdomains: true,
      },
    );
  });

  it('initializes wiki once while the same index window grows', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraph.mockReturnValue(of(codeGraph()));
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage({
      graphRevision: 'revision-example',
      totalNodes: 800,
    })));
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    fixture.componentInstance.growGraphWindow();

    expect(service.getWikiGraphStatus).toHaveBeenCalledTimes(1);
    expect(service.getCodeCompassGraph).toHaveBeenCalledTimes(2);
  });

  it('uses the content revision for graph-inventory coherence before metrics evidence', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    const graph = codeGraph('metrics-revision');
    graph.metadata.content_graph_revision = 'content-revision';
    service.getCodeCompassGraph.mockReturnValue(of(graph));
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage({
      graphRevision: 'content-revision',
    })));

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.codeGraphRevision()).toBe('content-revision');
    expect(fixture.componentInstance.graphInventoryRevision()).toBe('content-revision');
    expect(service.getCodeCompassGraph).toHaveBeenCalledTimes(1);
  });

  it('replaces an in-flight same-index wiki status poll', () => {
    vi.useFakeTimers();
    const firstPoll = new Subject<any>();
    const secondPoll = new Subject<any>();
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraph.mockReturnValue(of(codeGraph()));
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage()));
    service.getWikiGraphStatus
      .mockReturnValueOnce(of({ status: 'not_built' }))
      .mockReturnValueOnce(firstPoll)
      .mockReturnValueOnce(secondPoll);

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    fixture.componentInstance.build();
    vi.advanceTimersByTime(3000);
    expect(firstPoll.observed).toBe(true);

    fixture.componentInstance.build();
    expect(firstPoll.observed).toBe(false);
    vi.advanceTimersByTime(3000);
    firstPoll.next({ status: 'ready', generation: 'stale' });
    secondPoll.next({ status: 'ready', generation: 'current' });

    expect(fixture.componentInstance.status()).toEqual({
      status: 'ready',
      generation: 'current',
    });
  });

  it('replaces stale same-index ready-domain requests during a rebuild', () => {
    vi.useFakeTimers();
    const staleDomains = new Subject<any[]>();
    const currentDomains = new Subject<any[]>();
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraph.mockReturnValue(of(codeGraph()));
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage()));
    service.getWikiGraphStatus.mockReturnValue(of({ status: 'ready' }));
    service.getWikiDomainStatus
      .mockReturnValueOnce(of({ categories: { status: 'ready' } }))
      .mockReturnValueOnce(of({ categories: { status: 'ready' } }));
    service.getWikiDomains
      .mockReturnValueOnce(staleDomains)
      .mockReturnValueOnce(currentDomains);

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    expect(staleDomains.observed).toBe(true);

    fixture.componentInstance.buildDomain('categories');
    expect(staleDomains.observed).toBe(false);
    vi.advanceTimersByTime(3000);
    staleDomains.next([{ id: 'stale' }]);
    currentDomains.next([{ id: 'current' }]);

    expect(fixture.componentInstance.categoryDomains()).toEqual([{ id: 'current' }]);
  });

  it('shows source and unresolved relation totals without hiding staged records', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    const graph = codeGraph();
    Object.assign(graph.metadata, {
      global_source_edge_count: 9,
      global_total_edges: 5,
      global_unresolved_edge_count: 4,
      window_domain_group_count: 3,
      scope_domain_group_count: 8,
    });
    service.getCodeCompassGraph.mockReturnValue(of(graph));
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage()));

    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.globalEdgeTotal()).toBe(9);
    expect(fixture.componentInstance.globalUnresolvedEdgeTotal()).toBe(4);
    expect(fixture.nativeElement.querySelector('.ch-graph-info')?.textContent)
      .toContain('9 Quellrelationen');
    expect(fixture.nativeElement.querySelector('.ch-graph-info')?.textContent)
      .toContain('4 derzeit nicht renderbar');
    expect(fixture.nativeElement.querySelector('.ch-graph-info')?.textContent)
      .toContain('Bereiche im Fenster: 3 / 8');
  });

  it('cancels stale wiki status when the active source changes', () => {
    const oldGraph = new Subject<Record<string, any>>();
    const newGraph = new Subject<Record<string, any>>();
    const oldStatus = new Subject<any>();
    const newStatus = new Subject<any>();
    service.listKnowledgeIndexes.mockReturnValue(of([
      { id: 'conn-old', knowledge_index_id: 'index-old' },
      { id: 'conn-new', knowledge_index_id: 'index-new' },
    ]));
    service.getCodeCompassGraph
      .mockReturnValueOnce(oldGraph)
      .mockReturnValueOnce(newGraph);
    service.getCodeCompassGraphInventory.mockImplementation((connectionId: string) => of(
      inventoryPage({ graphRevision: connectionId === 'conn-old' ? 'revision-old' : 'revision-new' }),
    ));
    service.getWikiGraphStatus
      .mockReturnValueOnce(oldStatus)
      .mockReturnValueOnce(newStatus);
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    oldGraph.next(codeGraph('revision-old'));

    fixture.componentInstance.changeSource('conn-new');
    newGraph.next(codeGraph('revision-new'));
    oldStatus.next({ status: 'ready', source: 'old' });
    newStatus.next({ status: 'not_built', source: 'new' });

    expect(fixture.componentInstance.status()).toEqual({ status: 'not_built', source: 'new' });
    expect(oldStatus.observed).toBe(false);
  });

  it('keeps CodeCompass totals out of a Wiki subgraph view', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraph.mockReturnValue(of(codeGraph()));
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage({
      graphRevision: 'revision-example',
      totalNodes: 800,
    })));
    service.expandWikiArticle.mockReturnValue(of({
      nodes: [{ node_id: 'article:one' }],
      edges: [],
      metadata: { node_count: 1, edge_count: 0 },
    }));
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.ch-graph-info')).not.toBeNull();

    fixture.componentInstance.expand('one');
    fixture.detectChanges();

    expect(fixture.componentInstance.graphMode()).toBe('wiki');
    expect(fixture.nativeElement.querySelector('.ch-graph-info')).toBeNull();
  });

  it('rolls a failed detail request back to the confirmed graph window', () => {
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraph
      .mockReturnValueOnce(of(codeGraph()))
      .mockReturnValueOnce(throwError(() => new Error('unavailable')));
    service.getCodeCompassGraphInventory.mockReturnValue(of(inventoryPage({
      graphRevision: 'revision-example',
    })));
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    fixture.componentInstance.changeGraphLoadStrategy('detail');

    expect(fixture.componentInstance.confirmedNodeLimit()).toBe(100);
    expect(fixture.componentInstance.requestedNodeLimit()).toBe(100);
    expect(fixture.componentInstance.graphWindowAtLimit()).toBe(false);
    expect(fixture.componentInstance.error()).toContain('Fehler beim Laden');
  });

  it('reloads graph and inventory instead of mixing different revisions', () => {
    const firstGraph = new Subject<Record<string, any>>();
    const secondGraph = new Subject<Record<string, any>>();
    const firstInventory = new Subject<CodeCompassGraphInventoryPage>();
    const secondInventory = new Subject<CodeCompassGraphInventoryPage>();
    service.listKnowledgeIndexes.mockReturnValue(of([{
      id: 'conn-example',
      knowledge_index_id: 'index-example',
    }]));
    service.getCodeCompassGraph
      .mockReturnValueOnce(firstGraph)
      .mockReturnValueOnce(secondGraph);
    service.getCodeCompassGraphInventory
      .mockReturnValueOnce(firstInventory)
      .mockReturnValueOnce(secondInventory);
    const fixture = TestBed.createComponent(CodehugWikiGraphComponent);
    fixture.detectChanges();

    firstGraph.next(codeGraph('revision-old'));
    firstInventory.next(inventoryPage({ graphRevision: 'revision-new' }));
    expect(fixture.componentInstance.graphDomains()).toEqual([]);
    secondGraph.next(codeGraph('revision-new'));
    secondInventory.next(inventoryPage({
      graphRevision: 'revision-new',
      totalNodes: 42,
    }));

    expect(service.getCodeCompassGraph).toHaveBeenCalledTimes(2);
    expect(fixture.componentInstance.codeGraphRevision()).toBe('revision-new');
    expect(fixture.componentInstance.graphInventoryRevision()).toBe('revision-new');
    expect(fixture.componentInstance.graphInventoryNodeTotal()).toBe(42);
    expect(fixture.componentInstance.graphDomainError()).toBe('');
  });

});
