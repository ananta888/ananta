import { Injectable, inject } from '@angular/core';
import { EMPTY, Observable, concat, defer } from 'rxjs';
import { expand, map } from 'rxjs/operators';

import type { CodeCompassGraphV1 } from '../../../models/source-control-contracts';
import { SourceControlV1HttpError } from '../../../services/source-control-v1-api.client';
import {
  InternalsService,
  type CodeCompassGraphStage,
  type CodeCompassGraphStagedPage,
} from './internals.service';

export const FULL_GRAPH_NODE_PAGE_SIZE = 500;
export const FULL_GRAPH_EDGE_PAGE_SIZE = 2_000;

export interface CodeCompassFullGraphLoadRequest {
  readonly connectionId: string;
  readonly domainScope?: string;
  readonly includeSubdomains: boolean;
  readonly expectedRevision?: string;
}

export interface CodeCompassFullGraphProgress {
  readonly stage: CodeCompassGraphStage;
  readonly loadedNodes: number;
  readonly totalNodes: number;
  readonly loadedEdges: number;
  readonly totalEdges: number;
}

export type CodeCompassFullGraphLoadEvent =
  | {
      readonly kind: 'progress';
      readonly progress: CodeCompassFullGraphProgress;
    }
  | {
      readonly kind: 'complete';
      readonly graph: CodeCompassGraphV1;
      readonly graphRevision: string;
      readonly nodeCount: number;
      readonly edgeCount: number;
    };

export type CodeCompassFullGraphLoadFailureReason =
  | 'revision_changed'
  | 'source_changed'
  | 'scope_changed'
  | 'cursor_repeated'
  | 'cursor_without_progress'
  | 'total_changed'
  | 'terminal_before_total'
  | 'cursor_after_total'
  | 'delivery_exceeds_total'
  | 'duplicate_record';

export class CodeCompassFullGraphLoadError extends Error {
  constructor(readonly reason: CodeCompassFullGraphLoadFailureReason) {
    super(reason);
    this.name = 'CodeCompassFullGraphLoadError';
  }
}

class StageCursorGuard {
  private readonly seenCursors = new Set<string>();
  private expectedTotal: number | null = null;
  private delivered = 0;

  accept(page: CodeCompassGraphStagedPage): number {
    if (this.expectedTotal === null) this.expectedTotal = page.total;
    if (page.total !== this.expectedTotal) {
      throw new CodeCompassFullGraphLoadError('total_changed');
    }
    if (page.returned === 0 && page.nextCursor !== null) {
      throw new CodeCompassFullGraphLoadError('cursor_without_progress');
    }
    this.delivered += page.returned;
    if (this.delivered > this.expectedTotal) {
      throw new CodeCompassFullGraphLoadError('delivery_exceeds_total');
    }
    if (page.nextCursor === null) {
      if (this.delivered !== this.expectedTotal) {
        throw new CodeCompassFullGraphLoadError('terminal_before_total');
      }
      return this.delivered;
    }
    if (this.delivered >= this.expectedTotal) {
      throw new CodeCompassFullGraphLoadError('cursor_after_total');
    }
    if (this.seenCursors.has(page.nextCursor)) {
      throw new CodeCompassFullGraphLoadError('cursor_repeated');
    }
    this.seenCursors.add(page.nextCursor);
    return this.delivered;
  }

  total(): number {
    return this.expectedTotal ?? 0;
  }
}

interface StreamBinding {
  readonly revision: string;
  readonly sourceRef: string;
  readonly indexId: string;
  readonly domainScope: string | null;
  readonly includeSubdomains: boolean;
  readonly totalNodes: number;
  readonly totalEdges: number;
}

/**
 * Reassembles the lossless staged transport into one atomic graph snapshot.
 * It owns pagination and coherence; consumers only render the completed event.
 */
@Injectable({ providedIn: 'root' })
export class CodeCompassFullGraphLoaderService {
  private readonly internals = inject(InternalsService);

  load(request: CodeCompassFullGraphLoadRequest): Observable<CodeCompassFullGraphLoadEvent> {
    return new Observable(subscriber => {
      const nodes = new Map<string, Readonly<Record<string, unknown>>>();
      const edges = new Map<string, Readonly<Record<string, unknown>>>();
      const guards: Record<CodeCompassGraphStage, StageCursorGuard> = {
        nodes: new StageCursorGuard(),
        edges: new StageCursorGuard(),
      };
      const warnings = new Set<string>();
      let binding: StreamBinding | null = null;
      let template: CodeCompassGraphV1 | null = null;
      let envelope: Record<string, unknown> = {};
      let metadata: Record<string, unknown> = {};
      let diagnostics: Record<string, unknown> = {};
      let nodeMetricCapabilities: Record<string, unknown> = {};

      const accept = (page: CodeCompassGraphStagedPage): CodeCompassGraphStagedPage => {
        binding = this.bindPage(binding, page, request);
        const loaded = guards[page.stage].accept(page);
        template ??= page.graph;
        envelope = { ...envelope, ...(page.graph as unknown as Record<string, unknown>) };
        metadata = { ...metadata, ...page.graph.metadata };
        diagnostics = { ...diagnostics, ...page.graph.diagnostics };
        page.graph.warnings.forEach(warning => warnings.add(warning));
        if (page.stage === 'nodes') {
          this.addUniqueRecords(nodes, page.graph.nodes, 'node');
          nodeMetricCapabilities = this.mergeNodeMetricCapabilities(
            nodeMetricCapabilities,
            (page.graph as unknown as Record<string, unknown>)['metric_capabilities'],
          );
        } else {
          this.addUniqueRecords(edges, page.graph.edges, 'edge');
        }
        subscriber.next({
          kind: 'progress',
          progress: {
            stage: page.stage,
            loadedNodes: page.stage === 'nodes' ? loaded : guards.nodes.total(),
            totalNodes: binding.totalNodes,
            loadedEdges: page.stage === 'edges' ? loaded : 0,
            totalEdges: binding.totalEdges,
          },
        });
        return page;
      };

      const pages = (stage: CodeCompassGraphStage, pageSize: number) => {
        const fetch = (cursor?: string) => this.internals.getCodeCompassGraphStagedPage(
          request.connectionId,
          {
            stage,
            ...(cursor ? { cursor } : {}),
            pageSize,
            ...(request.domainScope ? { domainScope: request.domainScope } : {}),
            includeSubdomains: request.includeSubdomains,
          },
        ).pipe(map(accept));
        return defer(() => fetch()).pipe(
          expand(page => page.nextCursor === null ? EMPTY : fetch(page.nextCursor)),
        );
      };

      const transport = concat(
        pages('nodes', FULL_GRAPH_NODE_PAGE_SIZE),
        pages('edges', FULL_GRAPH_EDGE_PAGE_SIZE),
      ).subscribe({
        error: error => subscriber.error(this.normalizeTransportError(error)),
        complete: () => {
          if (!template || !binding) {
            subscriber.error(new CodeCompassFullGraphLoadError('terminal_before_total'));
            return;
          }
          if (
            nodes.size !== guards.nodes.total()
            || edges.size !== guards.edges.total()
            || nodes.size !== binding.totalNodes
            || edges.size !== binding.totalEdges
          ) {
            subscriber.error(new CodeCompassFullGraphLoadError('duplicate_record'));
            return;
          }
          const completeGraph = {
            ...envelope,
            nodes: [...nodes.values()],
            edges: [...edges.values()],
            metadata: {
              ...metadata,
              view: 'staged',
              stage: 'complete',
              content_graph_revision: binding.revision,
              node_count: nodes.size,
              edge_count: edges.size,
              scope_total_nodes: nodes.size,
              delivery_complete: true,
              delivery_returned: nodes.size + edges.size,
              delivery_total: nodes.size + edges.size,
              full_scope_loaded: true,
            },
            diagnostics,
            warnings: [...warnings],
            // Staged edge capabilities describe a transport page. Keep the
            // coherent node capabilities; GraphAdapter derives intrinsic edge
            // capabilities from the fully reassembled edge population.
            metric_capabilities: nodeMetricCapabilities,
            text_alternative: `Complete scoped graph with ${nodes.size} nodes and ${edges.size} edges.`,
          } as unknown as CodeCompassGraphV1;
          subscriber.next({
            kind: 'complete',
            graph: completeGraph,
            graphRevision: binding.revision,
            nodeCount: nodes.size,
            edgeCount: edges.size,
          });
          subscriber.complete();
        },
      });
      return () => transport.unsubscribe();
    });
  }

  private bindPage(
    current: StreamBinding | null,
    page: CodeCompassGraphStagedPage,
    request: CodeCompassFullGraphLoadRequest,
  ): StreamBinding {
    const graph = page.graph as unknown as Record<string, unknown>;
    const metadata = page.graph.metadata;
    const sourceRef = this.requiredText(graph['source_ref'], 'source_changed');
    const indexId = this.requiredText(metadata['knowledge_index_id'], 'source_changed');
    const domainScope = this.nullableText(metadata['domain_scope']);
    const includeSubdomains = metadata['include_subdomains'];
    if (typeof includeSubdomains !== 'boolean') {
      throw new CodeCompassFullGraphLoadError('scope_changed');
    }
    const totalNodes = this.count(metadata['total_nodes'], 'total_changed');
    const totalEdges = this.count(metadata['total_edges'], 'total_changed');
    if (page.total !== (page.stage === 'nodes' ? totalNodes : totalEdges)) {
      throw new CodeCompassFullGraphLoadError('total_changed');
    }
    const expectedDomainScope = request.domainScope ?? null;
    if (
      domainScope !== expectedDomainScope
      || includeSubdomains !== request.includeSubdomains
    ) {
      throw new CodeCompassFullGraphLoadError('scope_changed');
    }
    if (request.expectedRevision && page.graphRevision !== request.expectedRevision) {
      throw new CodeCompassFullGraphLoadError('revision_changed');
    }
    const incoming: StreamBinding = {
      revision: page.graphRevision,
      sourceRef,
      indexId,
      domainScope,
      includeSubdomains,
      totalNodes,
      totalEdges,
    };
    if (!current) return incoming;
    if (incoming.revision !== current.revision) {
      throw new CodeCompassFullGraphLoadError('revision_changed');
    }
    if (incoming.sourceRef !== current.sourceRef || incoming.indexId !== current.indexId) {
      throw new CodeCompassFullGraphLoadError('source_changed');
    }
    if (
      incoming.domainScope !== current.domainScope
      || incoming.includeSubdomains !== current.includeSubdomains
    ) {
      throw new CodeCompassFullGraphLoadError('scope_changed');
    }
    if (
      incoming.totalNodes !== current.totalNodes
      || incoming.totalEdges !== current.totalEdges
    ) {
      throw new CodeCompassFullGraphLoadError('total_changed');
    }
    return current;
  }

  private addUniqueRecords(
    target: Map<string, Readonly<Record<string, unknown>>>,
    records: readonly Readonly<Record<string, unknown>>[],
    kind: 'node' | 'edge',
  ): void {
    for (const record of records) {
      const identity = this.recordIdentity(record, kind);
      if (target.has(identity)) {
        throw new CodeCompassFullGraphLoadError('duplicate_record');
      }
      target.set(identity, record);
    }
  }

  private recordIdentity(
    record: Readonly<Record<string, unknown>>,
    kind: 'node' | 'edge',
  ): string {
    const value = kind === 'node'
      ? record['node_id']
      : record['edge_id'];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
    throw new CodeCompassFullGraphLoadError('duplicate_record');
  }

  private mergeNodeMetricCapabilities(
    current: Record<string, unknown>,
    raw: unknown,
  ): Record<string, unknown> {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return current;
    const merged = { ...current };
    for (const [metricId, capability] of Object.entries(raw)) {
      if (!capability || typeof capability !== 'object' || Array.isArray(capability)) continue;
      if ((capability as Record<string, unknown>)['entity'] === 'edge') continue;
      merged[metricId] ??= capability;
    }
    return merged;
  }

  private requiredText(
    value: unknown,
    reason: CodeCompassFullGraphLoadFailureReason,
  ): string {
    if (typeof value !== 'string' || !value.trim()) {
      throw new CodeCompassFullGraphLoadError(reason);
    }
    return value.trim();
  }

  private nullableText(value: unknown): string | null {
    if (value === null) return null;
    if (typeof value !== 'string' || !value.trim()) {
      throw new CodeCompassFullGraphLoadError('scope_changed');
    }
    return value.trim();
  }

  private count(
    value: unknown,
    reason: CodeCompassFullGraphLoadFailureReason,
  ): number {
    if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) {
      throw new CodeCompassFullGraphLoadError(reason);
    }
    return value;
  }

  private normalizeTransportError(error: unknown): unknown {
    if (
      error instanceof SourceControlV1HttpError
      && error.status === 409
      && error.reasonCode === 'graph_cursor_stale'
    ) {
      return new CodeCompassFullGraphLoadError('revision_changed');
    }
    return error;
  }
}
