import { TestBed } from '@angular/core/testing';
import { Observable, Subject, lastValueFrom, of, throwError } from 'rxjs';
import { toArray } from 'rxjs/operators';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { SourceControlV1HttpError } from '../../../services/source-control-v1-api.client';

import {
  CodeCompassFullGraphLoadError,
  CodeCompassFullGraphLoaderService,
} from './codecompass-full-graph-loader.service';
import {
  InternalsService,
  type CodeCompassGraphStagedPage,
} from './internals.service';

function page(
  stage: 'nodes' | 'edges',
  records: Array<Record<string, unknown>>,
  options: Partial<{
    cursor: string | null;
    total: number;
    revision: string;
    sourceRef: string;
    domainScope: string | null;
    includeSubdomains: boolean;
    totalNodes: number;
    totalEdges: number;
  }> = {},
): CodeCompassGraphStagedPage {
  const cursor = options.cursor ?? null;
  const total = options.total ?? records.length;
  const revision = options.revision ?? 'revision-1';
  const totalNodes = options.totalNodes ?? (stage === 'nodes' ? total : 3);
  const totalEdges = options.totalEdges ?? (stage === 'edges' ? total : 2);
  const metricCapabilities = stage === 'nodes'
    ? {
        in_degree: { entity: 'node', status: 'available' },
        confidence: { entity: 'edge', status: 'available' },
      }
    : { confidence: { entity: 'edge', status: 'available' } };
  const graph = {
    schema: 'domain_graph_artifact.v1',
    knowledge_index_id: 'index-1',
    source_kind: 'codecompass_graph',
    source_ref: options.sourceRef ?? 'index-1',
    nodes: stage === 'nodes' ? records : [],
    edges: stage === 'edges' ? records : [],
    metadata: {
      view: 'staged',
      stage,
      content_graph_revision: revision,
      knowledge_index_id: 'index-1',
      domain_scope: Object.prototype.hasOwnProperty.call(options, 'domainScope')
        ? options.domainScope
        : 'domain:agent',
      include_subdomains: options.includeSubdomains ?? true,
      next_cursor: cursor,
      delivery_returned: records.length,
      delivery_total: total,
      delivery_complete: cursor === null,
      total_nodes: totalNodes,
      total_edges: totalEdges,
    },
    diagnostics: { semantic_translation: { status: 'complete' } },
    metric_capabilities: metricCapabilities,
    warnings: stage === 'nodes' ? ['node warning'] : ['edge warning'],
  };
  return {
    graph: graph as never,
    stage,
    nextCursor: cursor,
    returned: records.length,
    total,
    graphRevision: revision,
    complete: cursor === null,
  };
}

describe('CodeCompassFullGraphLoaderService', () => {
  const internals = {
    getCodeCompassGraphStagedPage: vi.fn(),
  };
  let loader: CodeCompassFullGraphLoaderService;

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        CodeCompassFullGraphLoaderService,
        { provide: InternalsService, useValue: internals },
      ],
    });
    loader = TestBed.inject(CodeCompassFullGraphLoaderService);
  });

  it('assembles every node page before every edge page and emits one complete graph', async () => {
    internals.getCodeCompassGraphStagedPage.mockImplementation(
      (_connectionId: string, request: { stage: 'nodes' | 'edges'; cursor?: string }) => {
        if (request.stage === 'nodes' && !request.cursor) {
          return of(page('nodes', [{ node_id: 'a' }, { node_id: 'b' }], {
            cursor: 'node:2', total: 3,
          }));
        }
        if (request.stage === 'nodes') {
          return of(page('nodes', [{ node_id: 'c' }], { total: 3 }));
        }
        return of(page('edges', [
          { edge_id: 'e1', source_id: 'a', target_id: 'b' },
          { edge_id: 'e2', source_id: 'b', target_id: 'c' },
        ], { total: 2 }));
      },
    );

    const events = await lastValueFrom(loader.load({
      connectionId: 'connection-1',
      domainScope: 'domain:agent',
      includeSubdomains: true,
      expectedRevision: 'revision-1',
    }).pipe(toArray()));

    expect(internals.getCodeCompassGraphStagedPage.mock.calls.map(call => [
      call[1].stage,
      call[1].cursor ?? null,
      call[1].pageSize,
    ])).toEqual([
      ['nodes', null, 500],
      ['nodes', 'node:2', 500],
      ['edges', null, 2_000],
    ]);
    expect(events.slice(0, -1).map(event => event.kind)).toEqual([
      'progress', 'progress', 'progress',
    ]);
    const completed = events.at(-1);
    expect(completed?.kind).toBe('complete');
    if (completed?.kind !== 'complete') throw new Error('missing complete event');
    expect(completed.graph.nodes.map(node => node['node_id'])).toEqual(['a', 'b', 'c']);
    expect(completed.graph.edges.map(edge => edge['edge_id'])).toEqual(['e1', 'e2']);
    expect(completed.graph.metadata['full_scope_loaded']).toBe(true);
    expect((completed.graph as unknown as Record<string, any>)['metric_capabilities']).toEqual({
      in_degree: { entity: 'node', status: 'available' },
    });
    expect(completed.graph.warnings).toEqual(['node warning', 'edge warning']);
  });

  it('rejects overlapping record IDs instead of reporting a deduplicated graph as complete', async () => {
    internals.getCodeCompassGraphStagedPage.mockReturnValue(of(page('nodes', [
      { node_id: 'duplicate' },
      { node_id: 'duplicate' },
    ], { total: 2, totalNodes: 2, totalEdges: 0 })));

    await expect(lastValueFrom(loader.load({
      connectionId: 'connection-1',
      domainScope: 'domain:agent',
      includeSubdomains: true,
    }))).rejects.toMatchObject({ reason: 'duplicate_record' });
    expect(internals.getCodeCompassGraphStagedPage).toHaveBeenCalledTimes(1);
  });

  it('binds every page to the initial content revision and never requests edges after drift', async () => {
    internals.getCodeCompassGraphStagedPage
      .mockReturnValueOnce(of(page('nodes', [{ node_id: 'a' }], {
        cursor: 'node:1', total: 2,
      })))
      .mockReturnValueOnce(of(page('nodes', [{ node_id: 'b' }], {
        total: 2, revision: 'revision-2',
      })));

    await expect(lastValueFrom(loader.load({
      connectionId: 'connection-1',
      domainScope: 'domain:agent',
      includeSubdomains: true,
    }))).rejects.toMatchObject({ reason: 'revision_changed' });
    expect(internals.getCodeCompassGraphStagedPage).toHaveBeenCalledTimes(2);
  });

  it('stops a repeated cursor without entering a request loop', async () => {
    internals.getCodeCompassGraphStagedPage.mockReturnValue(of(page(
      'nodes',
      [{ node_id: 'a' }],
      { cursor: 'same-cursor', total: 3, totalNodes: 3 },
    )));

    await expect(lastValueFrom(loader.load({
      connectionId: 'connection-1',
      domainScope: 'domain:agent',
      includeSubdomains: true,
    }))).rejects.toMatchObject({ reason: 'cursor_repeated' });
    expect(internals.getCodeCompassGraphStagedPage).toHaveBeenCalledTimes(2);
  });

  it('cancels the active transport page when its consumer unsubscribes', () => {
    const activePage = new Subject<CodeCompassGraphStagedPage>();
    internals.getCodeCompassGraphStagedPage.mockReturnValue(
      activePage as Observable<CodeCompassGraphStagedPage>,
    );

    const subscription = loader.load({
      connectionId: 'connection-1',
      domainScope: 'domain:agent',
      includeSubdomains: true,
    }).subscribe();

    expect(activePage.observed).toBe(true);
    subscription.unsubscribe();
    expect(activePage.observed).toBe(false);
  });

  it('rejects a response whose canonical scope differs from the request', async () => {
    internals.getCodeCompassGraphStagedPage.mockReturnValue(of(page(
      'nodes',
      [{ node_id: 'a' }],
      { total: 1, domainScope: 'domain:other' },
    )));

    await expect(lastValueFrom(loader.load({
      connectionId: 'connection-1',
      domainScope: 'domain:agent',
      includeSubdomains: true,
    }))).rejects.toBeInstanceOf(CodeCompassFullGraphLoadError);
  });

  it('accepts an explicit whole-index stream bound to a null domain scope', async () => {
    internals.getCodeCompassGraphStagedPage.mockImplementation(
      (_connectionId: string, request: { stage: 'nodes' | 'edges' }) => of(
        request.stage === 'nodes'
          ? page('nodes', [{ node_id: 'a' }], {
              domainScope: null, totalNodes: 1, totalEdges: 0,
            })
          : page('edges', [], {
              domainScope: null, totalNodes: 1, totalEdges: 0,
            }),
      ),
    );

    const events = await lastValueFrom(loader.load({
      connectionId: 'connection-1',
      includeSubdomains: true,
    }).pipe(toArray()));

    expect(events.at(-1)?.kind).toBe('complete');
    expect(internals.getCodeCompassGraphStagedPage.mock.calls.every(call => (
      call[1].domainScope === undefined
    ))).toBe(true);
  });

  it.each([
    ['source', { sourceRef: 'index-2' }, 'source_changed'],
    ['totals', { totalEdges: 3 }, 'total_changed'],
  ])('rejects %s drift between node pages', async (_name, secondPatch, reason) => {
    internals.getCodeCompassGraphStagedPage
      .mockReturnValueOnce(of(page('nodes', [{ node_id: 'a' }], {
        cursor: 'node:1', total: 2,
      })))
      .mockReturnValueOnce(of(page('nodes', [{ node_id: 'b' }], {
        total: 2,
        ...secondPatch,
      })));

    await expect(lastValueFrom(loader.load({
      connectionId: 'connection-1',
      domainScope: 'domain:agent',
      includeSubdomains: true,
    }))).rejects.toMatchObject({ reason });
  });

  it('rejects a terminal page that has not delivered its declared total', async () => {
    internals.getCodeCompassGraphStagedPage.mockReturnValue(of(page(
      'nodes',
      [{ node_id: 'a' }],
      { total: 2, totalNodes: 2 },
    )));

    await expect(lastValueFrom(loader.load({
      connectionId: 'connection-1',
      domainScope: 'domain:agent',
      includeSubdomains: true,
    }))).rejects.toMatchObject({ reason: 'terminal_before_total' });
  });

  it('maps a stale-cursor HTTP conflict to an understandable revision failure', async () => {
    internals.getCodeCompassGraphStagedPage.mockReturnValue(throwError(
      () => new SourceControlV1HttpError(409, 'graph_cursor_stale'),
    ));

    await expect(lastValueFrom(loader.load({
      connectionId: 'connection-1',
      domainScope: 'domain:agent',
      includeSubdomains: true,
    }))).rejects.toMatchObject({ reason: 'revision_changed' });
  });
});
