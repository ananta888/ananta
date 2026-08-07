import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { EMPTY, Subject, Subscription, of } from 'rxjs';
import { debounceTime, distinctUntilChanged, expand, map, switchMap } from 'rxjs/operators';

import { GraphViewerComponent } from '../../codecompass-graph/components/graph-viewer/graph-viewer.component';
import {
  CodeCompassFullGraphLoadError,
  CodeCompassFullGraphLoaderService,
} from '../services/codecompass-full-graph-loader.service';
import { InternalsService } from '../services/internals.service';
import type {
  CodeCompassGraphDomainFacet,
  CodeCompassGraphInventoryPage,
  CodeCompassSemanticScopeEvidence,
} from '../services/internals.service';

type GraphLoadStrategyId = 'fast' | 'balanced' | 'detail';

interface GraphLoadStrategy {
  readonly id: GraphLoadStrategyId;
  readonly label: string;
  readonly initialNodes: number;
  readonly stepNodes: number;
}

const GRAPH_LOAD_STRATEGIES: readonly GraphLoadStrategy[] = Object.freeze([
  { id: 'fast', label: 'Schnellstart · 100', initialNodes: 100, stepNodes: 100 },
  { id: 'balanced', label: 'Ausgewogen · 250', initialNodes: 250, stepNodes: 125 },
  { id: 'detail', label: 'Detailfenster · 500', initialNodes: 500, stepNodes: 0 },
]);

// These bound only the optional topology preview. Complete staged loads page
// through the entire selected scope and do not use either value as a total cap.
const MAX_GRAPH_PREVIEW_NODES = 500;
const MAX_GRAPH_PREVIEW_EDGES = 2_000;

type FullGraphLoadState = 'idle' | 'nodes' | 'edges' | 'complete' | 'cancelled' | 'error';

type GraphDomainCursorInvalidReason =
  | 'cursor_repeated'
  | 'cursor_without_progress'
  | 'cursor_after_total'
  | 'terminal_before_total'
  | 'loaded_exceeds_total';

type GraphDomainCursorDecision =
  | { readonly kind: 'complete' }
  | { readonly kind: 'next'; readonly cursor: string }
  | {
      readonly kind: 'invalid';
      readonly reason: GraphDomainCursorInvalidReason;
    };

/** Validates cursor progress independently from component and transport state. */
class GraphDomainInventoryCursorGuard {
  private readonly requestedCursors = new Set<string>();
  private previousLoadedCount = 0;

  decide(
    page: CodeCompassGraphInventoryPage,
    loadedCount: number,
  ): GraphDomainCursorDecision {
    if (loadedCount > page.totalDomains) {
      return { kind: 'invalid', reason: 'loaded_exceeds_total' };
    }
    if (page.nextCursor === null) {
      return loadedCount === page.totalDomains
        ? { kind: 'complete' }
        : { kind: 'invalid', reason: 'terminal_before_total' };
    }
    if (this.requestedCursors.has(page.nextCursor)) {
      return { kind: 'invalid', reason: 'cursor_repeated' };
    }
    if (loadedCount <= this.previousLoadedCount) {
      return { kind: 'invalid', reason: 'cursor_without_progress' };
    }
    if (loadedCount >= page.totalDomains) {
      return { kind: 'invalid', reason: 'cursor_after_total' };
    }
    this.previousLoadedCount = loadedCount;
    this.requestedCursors.add(page.nextCursor);
    return { kind: 'next', cursor: page.nextCursor };
  }
}

/** One replaceable async operation with an explicit stale-response boundary. */
class WikiOperationSlot {
  private generation = 0;
  private request: Subscription | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;

  restart(): number {
    this.cancel();
    return this.generation;
  }

  isCurrent(generation: number): boolean {
    return generation === this.generation;
  }

  replaceRequest(generation: number, request: Subscription): void {
    if (!this.isCurrent(generation)) {
      request.unsubscribe();
      return;
    }
    this.request?.unsubscribe();
    this.request = request;
  }

  schedule(generation: number, callback: () => void, delayMs: number): void {
    if (!this.isCurrent(generation)) return;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      if (this.isCurrent(generation)) callback();
    }, delayMs);
  }

  cancel(): void {
    this.generation += 1;
    this.request?.unsubscribe();
    this.request = null;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
}

@Component({
  selector: 'ch-codehug-wiki-graph',
  standalone: true,
  imports: [GraphViewerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './codehug-wiki-graph.component.html',
  styleUrls: ['./codehug-internals.component.scss'],
})
export class CodehugWikiGraphComponent implements OnInit, OnDestroy {
  private readonly service = inject(InternalsService);
  private readonly fullGraphLoader = inject(CodeCompassFullGraphLoaderService);
  private readonly searchRequests = new Subject<string>();
  private indexSubscription: Subscription | null = null;
  private graphSubscription: Subscription | null = null;
  private wikiSubscriptions = new Subscription();
  private initializedWikiIndexId = '';
  private pendingInventoryRevision = '';
  private inventoryLoaded = false;
  private revisionRecoveryAttempted = false;
  private readonly wikiStatusOperation = new WikiOperationSlot();
  private readonly graphDomainInventoryOperation = new WikiOperationSlot();
  private readonly fullGraphOperation = new WikiOperationSlot();
  private readonly wikiDomainStatusBootstrapOperation = new WikiOperationSlot();
  private readonly wikiBuildOperation = new WikiOperationSlot();
  private readonly wikiDomainBuildOperations = new Map<string, WikiOperationSlot>();
  private readonly wikiDomainPollOperations = new Map<string, WikiOperationSlot>();
  private readonly wikiReadyDomainOperations = new Map<string, WikiOperationSlot>();

  readonly indexes = signal<any[]>([]);
  readonly selectedConnectionId = signal('');
  readonly rawGraph = signal<any>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly metadata = signal<Record<string, unknown> | null>(null);
  readonly graphMode = signal<'code' | 'wiki'>('code');
  readonly graphDomains = signal<readonly CodeCompassGraphDomainFacet[]>([]);
  readonly selectedGraphDomain = signal('');
  readonly includeGraphSubdomains = signal(true);
  readonly graphDomainNextCursor = signal<string | null>(null);
  readonly graphDomainTotal = signal(0);
  readonly graphInventoryNodeTotal = signal(0);
  readonly graphInventoryEdgeTotal = signal(0);
  readonly graphDomainLoading = signal(false);
  readonly graphDomainError = signal('');
  readonly graphLoadStrategy = signal<GraphLoadStrategyId>('fast');
  readonly requestedNodeLimit = signal(100);
  readonly confirmedNodeLimit = signal(0);
  readonly codeGraphRevision = signal('');
  readonly codeGraphEvidenceRevision = signal('');
  readonly graphInventoryRevision = signal('');
  readonly fullGraphLoadState = signal<FullGraphLoadState>('idle');
  readonly fullGraphLoadedNodes = signal(0);
  readonly fullGraphTotalNodes = signal(0);
  readonly fullGraphLoadedEdges = signal(0);
  readonly fullGraphTotalEdges = signal(0);
  readonly fullGraphSemanticScope = signal<Readonly<CodeCompassSemanticScopeEvidence> | null>(null);
  readonly fullGraphSemanticScopeComplete = computed(() => (
    Boolean(this.selectedGraphDomain())
    && this.fullGraphSemanticScope()?.complete === true
  ));
  readonly fullGraphSemanticScopeStatus = computed(() => (
    this.fullGraphSemanticScope()?.status ?? 'unverified'
  ));
  readonly semanticScopeToolbarLabel = computed(() => {
    if (this.fullGraphSemanticScopeComplete()) return 'Domain vollständig geladen';
    switch (this.fullGraphSemanticScopeStatus()) {
      case 'unavailable': return 'Transport vollständig · Semantik nicht verfügbar';
      case 'partial': return 'Transport vollständig · Semantik unvollständig';
      default: return 'Transport vollständig · Semantik nicht verifiziert';
    }
  });
  readonly fullGraphLoading = computed(() => (
    this.fullGraphLoadState() === 'nodes' || this.fullGraphLoadState() === 'edges'
  ));
  readonly fullGraphProgress = computed(() => {
    const state = this.fullGraphLoadState();
    if (state === 'nodes') {
      return `Scope-Transport: Knoten ${this.fullGraphLoadedNodes()} / ${this.fullGraphTotalNodes() || '…'}`;
    }
    if (state === 'edges') {
      return `Scope-Transport: Knoten ${this.fullGraphLoadedNodes()} / ${this.fullGraphTotalNodes()} · Kanten ${this.fullGraphLoadedEdges()} / ${this.fullGraphTotalEdges() || '…'}`;
    }
    if (state === 'complete') {
      const label = this.selectedGraphDomain() ? 'Domain-Scope' : 'Basisindex';
      return `${label} vollständig übertragen: ${this.fullGraphLoadedNodes()} Knoten · ${this.fullGraphLoadedEdges()} Kanten`;
    }
    if (state === 'cancelled') return 'Scope-Transport abgebrochen';
    return '';
  });
  readonly graphDomainInventoryProgress = computed(() => {
    const loaded = this.graphDomains().length;
    const total = this.graphDomainTotal();
    const totalLabel = total > 0 || this.graphInventoryRevision()
      ? String(total)
      : 'unbekannt';
    const loadingLabel = this.graphDomainLoading()
      ? ' · weitere Seiten werden automatisch geladen…'
      : '';
    return `Domain-Inventar: ${loaded} / ${totalLabel} Bereiche geladen${loadingLabel}`;
  });
  readonly loadStrategies = GRAPH_LOAD_STRATEGIES;
  readonly selectedIndex = computed(
    () => this.indexes().find(index => index.id === this.selectedConnectionId()) ?? null,
  );
  readonly selectedKnowledgeIndexId = computed(
    () => String(this.selectedIndex()?.knowledge_index_id ?? '').trim(),
  );
  readonly loadedNodeCount = computed(() => this.graphItemCount('nodes', 'entities'));
  readonly loadedEdgeCount = computed(() => this.graphItemCount('edges', 'relations'));
  readonly scopeNodeTotal = computed(() => this.metadataCount(
    'scope_total_nodes',
    'total_nodes',
  ));
  readonly globalNodeTotal = computed(() => this.metadataCount(
    'global_total_nodes',
  ) || this.graphInventoryNodeTotal() || this.scopeNodeTotal());
  readonly globalEdgeTotal = computed(() => this.metadataCountOrNull(
    'global_source_edge_count',
    'global_total_edges',
    'total_edges',
  ) ?? this.graphInventoryEdgeTotal());
  readonly globalUnresolvedEdgeTotal = computed(() => this.metadataCount(
    'global_unresolved_edge_count',
  ));
  readonly scopeBoundaryEdgeCount = computed(() => this.metadataCount(
    'scope_boundary_edge_count',
  ));
  readonly scopeInternalEdgeCount = computed(() => this.metadataCount(
    'internal_edge_count',
  ));
  readonly scopeUnresolvedEdgeCount = computed(() => this.metadataCount(
    'scope_unresolved_edge_count',
  ));
  readonly graphDomainWindowStats = computed(() => {
    const windowCount = this.metadataCountOrNull('window_domain_group_count');
    const scopeCount = this.metadataCountOrNull('scope_domain_group_count');
    return windowCount === null || scopeCount === null
      ? null
      : { windowCount, scopeCount };
  });
  readonly semanticScopeNotice = computed(() => {
    if (this.fullGraphLoadState() !== 'complete') return '';
    if (!this.selectedGraphDomain()) {
      return 'Basisindex vollständig übertragen; semantische Vollständigkeit wird nur für ausgewählte Domains verifiziert.';
    }
    const evidence = this.fullGraphSemanticScope();
    if (!evidence) {
      return 'Transport vollständig, Semantik nicht verifiziert.';
    }
    if (evidence.complete === true) {
      return `Adapter-Evidenz vollständig: ${evidence.supplementNodeCount.toLocaleString('de-DE')} Symbole · ${evidence.supplementEdgeCount.toLocaleString('de-DE')} semantische Relationen.`;
    }
    if (evidence.status === 'unavailable') {
      return 'Transport vollständig, semantisches Supplement nicht verfügbar.';
    }
    return 'Transport vollständig, semantisches Supplement unvollständig.';
  });
  readonly activeGraphLoadStep = computed(() => Math.max(
    1,
    this.activeLoadStrategy().stepNodes || 100,
  ));
  readonly remainingScopeNodes = computed(() => Math.max(
    0,
    this.scopeNodeTotal() - this.loadedNodeCount(),
  ));
  readonly canGrowGraphWindow = computed(() => (
    this.remainingScopeNodes() > 0
    && this.confirmedNodeLimit() < MAX_GRAPH_PREVIEW_NODES
    && !this.loading()
    && !this.fullGraphLoading()
  ));
  readonly graphWindowAtLimit = computed(() => (
    this.remainingScopeNodes() > 0
    && this.confirmedNodeLimit() >= MAX_GRAPH_PREVIEW_NODES
    && !this.fullGraphLoading()
  ));

  readonly status = signal<any>(null);
  readonly searchQuery = signal('');
  readonly searchResults = signal<Array<{ slug: string; title: string }>>([]);
  readonly expandedSlug = signal('');
  readonly domainStatus = signal<any>(null);
  readonly hubDomains = signal<any[]>([]);
  readonly categoryDomains = signal<any[]>([]);
  readonly clusterDomains = signal<any[]>([]);

  ngOnInit(): void {
    this.loading.set(true);
    this.indexSubscription = this.service.listKnowledgeIndexes().subscribe({
      next: indexes => {
        this.indexes.set([...indexes]);
        const firstConnectionId = String(indexes[0]?.['id'] || '').trim();
        if (!firstConnectionId) {
          this.loading.set(false);
          this.error.set('Keine kanonische Connection mit aktivem Index verfügbar');
          return;
        }
        this.selectedConnectionId.set(firstConnectionId);
        this.loadGraph();
      },
      error: () => {
        this.loading.set(false);
        this.error.set('Aktive Projektindizes konnten nicht geladen werden');
      },
    });
  }

  ngOnDestroy(): void {
    this.indexSubscription?.unsubscribe();
    this.graphSubscription?.unsubscribe();
    this.graphDomainInventoryOperation.cancel();
    this.fullGraphOperation.cancel();
    this.cancelWikiContext();
    this.searchRequests.complete();
  }

  loadGraph(clearGraph = false): void {
    const connectionId = this.selectedConnectionId();
    if (!connectionId) {
      this.error.set('Keine projektgebundene Connection ausgewählt');
      return;
    }
    this.cancelFullGraphLoad(false);
    this.graphSubscription?.unsubscribe();
    const transitioningFromWiki = this.graphMode() !== 'code';
    const resetRenderedGraph = clearGraph || transitioningFromWiki;
    const rollbackLimit = resetRenderedGraph ? 0 : this.confirmedNodeLimit();
    this.loading.set(true);
    this.error.set('');
    if (resetRenderedGraph) {
      this.rawGraph.set(null);
      this.metadata.set(null);
      this.confirmedNodeLimit.set(0);
    }
    const limit = this.requestedNodeLimit();
    const domainScope = this.selectedGraphDomain();
    const includeSubdomains = this.includeGraphSubdomains();
    this.graphSubscription = this.service.getCodeCompassGraph(connectionId, {
      limit,
      maxEdges: this.edgeLimitFor(limit),
      ...(domainScope ? { domainScope } : {}),
      includeSubdomains,
    }).subscribe({
      next: graph => {
        if (!this.graphRequestIsCurrent(connectionId, domainScope, includeSubdomains)) return;
        this.loading.set(false);
        if (!graph) return this.error.set('Quellgraph nicht verfügbar');
        this.graphMode.set('code');
        this.rawGraph.set(graph);
        this.metadata.set(graph?.metadata ?? null);
        const confirmedLimit = this.metadataCountFrom(
          graph?.metadata,
          'window_node_limit',
        ) ?? limit;
        this.confirmedNodeLimit.set(Math.min(MAX_GRAPH_PREVIEW_NODES, confirmedLimit));
        this.requestedNodeLimit.set(this.confirmedNodeLimit());
        const revision = this.codeGraphContentRevision(graph);
        const evidenceRevision = this.stableGraphEvidenceRevision(graph);
        const previousRevision = this.codeGraphRevision();
        const previousEvidenceRevision = this.codeGraphEvidenceRevision();
        this.codeGraphRevision.set(revision);
        this.codeGraphEvidenceRevision.set(evidenceRevision);
        if (
          (previousRevision && revision && previousRevision !== revision)
          || (
            previousEvidenceRevision
            && evidenceRevision
            && previousEvidenceRevision !== evidenceRevision
          )
        ) {
          this.clearGraphInventoryState();
        }
        this.ensureGraphDomainInventory(connectionId, revision);
        const nodeCount = Number(
          graph?.metadata?.node_count ?? graph?.nodes?.length ?? 0,
        );
        if (!Number.isFinite(nodeCount) || nodeCount <= 0) {
          this.error.set(
            domainScope
              ? 'Dieser Domain-Bereich enthält in der gewählten Subdomain-Einstellung keine Knoten.'
              : 'Der aktive Index enthält keinen Quellgraphen. Quelle mit dem Profil "Deep Code" neu indexieren.',
          );
          return;
        }
        const knowledgeIndexId = this.selectedKnowledgeIndexId();
        if (knowledgeIndexId) this.initializeWiki(knowledgeIndexId);
      },
      error: () => {
        if (this.graphRequestIsCurrent(connectionId, domainScope, includeSubdomains)) {
          this.loading.set(false);
          if (rollbackLimit > 0) {
            this.confirmedNodeLimit.set(rollbackLimit);
            this.requestedNodeLimit.set(rollbackLimit);
          }
          this.error.set('Fehler beim Laden des projektgebundenen Quellgraphen');
        }
      },
    });
  }

  changeSource(value: string): void {
    this.selectedConnectionId.set(value);
    this.resetViewState();
    this.requestedNodeLimit.set(this.activeLoadStrategy().initialNodes);
    this.loadGraph();
  }

  changeGraphDomain(value: string): void {
    this.selectedGraphDomain.set(value);
    if (value) {
      this.loadFullGraphScope();
      return;
    }
    this.requestedNodeLimit.set(this.activeLoadStrategy().initialNodes);
    this.loadGraph(true);
  }

  changeGraphSubdomainPolicy(include: boolean): void {
    this.includeGraphSubdomains.set(include);
    if (!this.selectedGraphDomain()) return;
    this.loadFullGraphScope();
  }

  changeGraphLoadStrategy(value: string): void {
    const strategy = GRAPH_LOAD_STRATEGIES.find(candidate => candidate.id === value);
    if (!strategy) return;
    this.graphLoadStrategy.set(strategy.id);
    this.requestedNodeLimit.set(strategy.initialNodes);
    if (this.selectedGraphDomain()) return;
    this.loadGraph();
  }

  /**
   * Loads a selected domain automatically, or the complete admitted base index
   * after an explicit request. Only selected domains receive supplement-backed
   * semantic completeness evidence. Transport page sizes are not graph limits.
   */
  loadFullGraphScope(): void {
    const connectionId = this.selectedConnectionId();
    if (!connectionId) {
      this.error.set('Keine projektgebundene Connection ausgewählt');
      return;
    }
    const domainScope = this.selectedGraphDomain();
    const includeSubdomains = this.includeGraphSubdomains();
    const expectedEvidenceRevision = this.codeGraphEvidenceRevision();
    const generation = this.fullGraphOperation.restart();
    this.graphSubscription?.unsubscribe();
    this.loading.set(false);
    this.error.set('');
    this.graphMode.set('code');
    this.fullGraphLoadState.set('nodes');
    this.fullGraphLoadedNodes.set(0);
    this.fullGraphTotalNodes.set(this.expectedFullGraphNodeTotal(domainScope));
    this.fullGraphLoadedEdges.set(0);
    this.fullGraphTotalEdges.set(0);
    this.fullGraphSemanticScope.set(null);
    if (domainScope) {
      this.rawGraph.set(null);
      this.metadata.set(null);
    }
    const request = this.fullGraphLoader.load({
      connectionId,
      ...(domainScope ? { domainScope } : {}),
      includeSubdomains,
      ...(expectedEvidenceRevision ? { expectedEvidenceRevision } : {}),
    }).subscribe({
      next: event => {
        if (
          !this.fullGraphOperation.isCurrent(generation)
          || !this.graphRequestIsCurrent(connectionId, domainScope, includeSubdomains)
        ) return;
        if (event.kind === 'progress') {
          this.fullGraphLoadState.set(event.progress.stage);
          this.fullGraphLoadedNodes.set(event.progress.loadedNodes);
          this.fullGraphTotalNodes.set(event.progress.totalNodes);
          this.fullGraphLoadedEdges.set(event.progress.loadedEdges);
          this.fullGraphTotalEdges.set(event.progress.totalEdges);
          return;
        }
        this.rawGraph.set(event.graph);
        this.metadata.set(event.graph.metadata);
        this.confirmedNodeLimit.set(event.nodeCount);
        this.fullGraphLoadedNodes.set(event.nodeCount);
        this.fullGraphLoadedEdges.set(event.edgeCount);
        this.fullGraphSemanticScope.set(event.semanticScope);
        this.fullGraphLoadState.set('complete');
        if (event.evidenceGraphRevision) {
          this.codeGraphEvidenceRevision.set(event.evidenceGraphRevision);
        }
        if (!domainScope) {
          this.codeGraphRevision.set(event.graphRevision);
          this.ensureGraphDomainInventory(connectionId, event.graphRevision);
        }
        if (event.nodeCount === 0) {
          this.error.set(
            domainScope
              ? 'Dieser Domain-Bereich enthält in der gewählten Subdomain-Einstellung keine Knoten.'
              : 'Der aktive Index enthält keinen Quellgraphen.',
          );
          return;
        }
        const knowledgeIndexId = this.selectedKnowledgeIndexId();
        if (knowledgeIndexId) this.initializeWiki(knowledgeIndexId);
      },
      error: error => {
        if (!this.fullGraphOperation.isCurrent(generation)) return;
        this.fullGraphLoadState.set('error');
        this.error.set(this.fullGraphLoadErrorMessage(error));
      },
    });
    this.fullGraphOperation.replaceRequest(generation, request);
  }

  cancelFullGraphLoad(markCancelled = true): void {
    const wasLoading = this.fullGraphLoading();
    this.fullGraphOperation.cancel();
    if (markCancelled && wasLoading) {
      this.fullGraphLoadState.set('cancelled');
      return;
    }
    if (!markCancelled) this.resetFullGraphProgress();
  }

  returnToGraphPreview(): void {
    this.requestedNodeLimit.set(this.activeLoadStrategy().initialNodes);
    this.loadGraph(true);
  }

  refreshGraphScopeAfterFullLoadError(): void {
    // Domain keys are opaque and revision-bound. Rebuild the inventory from a
    // global preview instead of reusing a key issued by the stale revision.
    this.selectedGraphDomain.set('');
    this.returnToGraphPreview();
  }

  growGraphWindow(): void {
    if (!this.canGrowGraphWindow()) return;
    const step = this.activeGraphLoadStep();
    this.requestedNodeLimit.set(Math.min(
      MAX_GRAPH_PREVIEW_NODES,
      this.confirmedNodeLimit() + step,
    ));
    this.loadGraph();
  }

  retryGraphDomainInventory(): void {
    if (!this.selectedConnectionId() || this.graphDomainLoading()) return;
    this.loadGraphDomainInventory(this.codeGraphRevision());
  }

  graphDomainOptionLabel(domain: CodeCompassGraphDomainFacet): string {
    const indentation = '— '.repeat(Math.min(Math.max(domain.depth, 0), 7));
    const count = this.includeGraphSubdomains()
      ? domain.subtreeNodeCount
      : domain.directNodeCount;
    const fullPath = domain.source === 'unassigned'
      ? domain.label
      : domain.path;
    const semanticCount = this.includeGraphSubdomains()
      && domain.semanticScopeStatus === 'available'
      ? domain.semanticNodeCount
      : undefined;
    const countLabel = semanticCount === undefined
      ? count.toLocaleString('de-DE')
      : `${(domain.baseNodeCount ?? count).toLocaleString('de-DE')} Struktur + ${semanticCount.toLocaleString('de-DE')} Symbole`;
    return `${indentation}${fullPath} · ${this.graphDomainSourceLabel(domain.source)} (${countLabel})`;
  }

  search(query: string): void {
    this.cancelFullGraphLoad(false);
    this.searchQuery.set(query);
    if (!query) {
      this.rawGraph.set(null);
      this.metadata.set(null);
      this.expandedSlug.set('');
    }
    this.searchRequests.next(query);
  }

  expand(slug: string): void {
    const indexId = this.selectedKnowledgeIndexId();
    if (!indexId) return;
    this.cancelFullGraphLoad(false);
    this.expandedSlug.set(slug);
    this.searchResults.set([]);
    this.searchQuery.set('');
    this.loading.set(true);
    this.error.set('');
    this.graphSubscription?.unsubscribe();
    this.graphSubscription = this.service.expandWikiArticle(indexId, slug).subscribe({
      next: graph => {
        if (this.selectedKnowledgeIndexId() !== indexId) return;
        this.loading.set(false);
        if (!graph?.nodes?.length) return this.error.set('Keine Nachbarn gefunden');
        this.graphMode.set('wiki');
        this.rawGraph.set(graph);
        this.metadata.set(graph.metadata ?? null);
      },
      error: () => {
        if (this.selectedKnowledgeIndexId() === indexId) {
          this.loading.set(false);
          this.error.set('Fehler beim Laden');
        }
      },
    });
  }

  selectDomain(mode: string, domainId: string): void {
    if (!domainId) return;
    if (mode === 'hubs') return this.expand(domainId);
    const indexId = this.selectedKnowledgeIndexId();
    if (!indexId) return;
    this.cancelFullGraphLoad(false);
    this.loading.set(true);
    this.error.set('');
    this.graphSubscription?.unsubscribe();
    this.graphSubscription = this.service.getWikiDomainGraph(indexId, mode, domainId).subscribe({
      next: graph => {
        if (this.selectedKnowledgeIndexId() !== indexId) return;
        this.loading.set(false);
        if (!graph?.nodes?.length) return this.error.set('Keine Artikel in dieser Domäne');
        this.graphMode.set('wiki');
        this.rawGraph.set(graph);
        this.metadata.set(graph.metadata ?? null);
      },
      error: () => {
        if (this.selectedKnowledgeIndexId() === indexId) {
          this.loading.set(false);
          this.error.set('Fehler beim Laden');
        }
      },
    });
  }

  build(force = false): void {
    const indexId = this.selectedKnowledgeIndexId();
    if (!indexId) return;
    this.status.set({ status: 'building' });
    this.wikiStatusOperation.cancel();
    const generation = this.wikiBuildOperation.restart();
    const subscription = this.service.triggerWikiGraphBuild(indexId, force).subscribe({
      next: () => {
        if (
          this.wikiBuildOperation.isCurrent(generation)
          && this.wikiIndexIsCurrent(indexId)
        ) {
          this.pollStatus(indexId);
        }
      },
      error: () => {
        if (
          this.wikiBuildOperation.isCurrent(generation)
          && this.wikiIndexIsCurrent(indexId)
        ) {
          this.status.set({ status: 'error' });
          this.error.set('Wiki-Graph-Build konnte nicht gestartet werden');
        }
      },
    });
    this.wikiBuildOperation.replaceRequest(generation, subscription);
  }

  buildDomain(mode: string): void {
    const indexId = this.selectedKnowledgeIndexId();
    if (!indexId) return;
    this.domainStatus.update(current => ({ ...(current ?? {}), [mode]: { status: 'building' } }));
    this.wikiDomainStatusBootstrapOperation.cancel();
    this.operationFor(this.wikiReadyDomainOperations, mode).cancel();
    this.operationFor(this.wikiDomainPollOperations, mode).cancel();
    const operation = this.operationFor(this.wikiDomainBuildOperations, mode);
    const generation = operation.restart();
    const subscription = this.service.buildWikiDomains(indexId, mode).subscribe({
      next: () => {
        if (operation.isCurrent(generation) && this.wikiIndexIsCurrent(indexId)) {
          this.pollDomainStatus(indexId, mode);
        }
      },
      error: () => {
        if (operation.isCurrent(generation) && this.wikiIndexIsCurrent(indexId)) {
          this.domainStatus.update(current => ({
            ...(current ?? {}),
            [mode]: { status: 'error' },
          }));
          this.error.set('Domain-Build konnte nicht gestartet werden');
        }
      },
    });
    operation.replaceRequest(generation, subscription);
  }

  domainModeStatus(mode: string): string {
    return this.domainStatus()?.[mode]?.status ?? 'not_built';
  }

  indexLabel(index: any): string {
    const source = index?.index_metadata?.source_id ?? index?.collection_id ?? index?.id ?? '?';
    return `${index?.source_scope ?? 'Index'}: ${String(source).replace(/[-_]/g, ' ')}`;
  }

  private initializeWiki(indexId: string): void {
    if (this.initializedWikiIndexId === indexId) return;
    this.cancelWikiContext();
    this.initializedWikiIndexId = indexId;
    const generation = this.wikiStatusOperation.restart();
    const statusSubscription = this.service.getWikiGraphStatus(indexId).subscribe(status => {
      if (
        !this.wikiStatusOperation.isCurrent(generation)
        || !this.wikiIndexIsCurrent(indexId)
      ) return;
      this.status.set(status);
      if (status?.status === 'ready') {
        this.loadWikiDomainStatus(indexId);
      }
    });
    this.wikiStatusOperation.replaceRequest(generation, statusSubscription);
    const searchSubscription = this.searchRequests.pipe(
      debounceTime(300),
      distinctUntilChanged(),
      switchMap(query => query
        ? this.service.searchWikiArticles(indexId, query).pipe(
            map(results => ({ query, results })),
          )
        : of({ query, results: [] as Array<{ slug: string; title: string }> })),
    ).subscribe(({ query, results }) => {
      if (!this.wikiIndexIsCurrent(indexId) || this.searchQuery() !== query) return;
      this.searchResults.set(results);
    });
    this.wikiSubscriptions.add(searchSubscription);
  }

  private loadGraphDomainInventory(expectedRevision = this.codeGraphRevision()): void {
    const connectionId = this.selectedConnectionId();
    if (!connectionId) return;
    const generation = this.graphDomainInventoryOperation.restart();
    const cursorGuard = new GraphDomainInventoryCursorGuard();
    let paginationCompleted = false;
    let paginationInvalid = false;
    this.clearGraphInventoryState(false);
    this.pendingInventoryRevision = expectedRevision;
    this.graphDomainLoading.set(true);
    this.graphDomainError.set('');
    const request = this.service.getCodeCompassGraphInventory(
      connectionId,
      undefined,
    ).pipe(
      expand(page => {
        if (
          !this.graphDomainInventoryOperation.isCurrent(generation)
          || this.selectedConnectionId() !== connectionId
          || paginationInvalid
        ) {
          return EMPTY;
        }
        const decision = cursorGuard.decide(page, this.graphDomains().length);
        if (decision.kind === 'complete') {
          paginationCompleted = true;
          return EMPTY;
        }
        if (decision.kind === 'invalid') {
          paginationInvalid = true;
          this.graphDomainNextCursor.set(null);
          this.graphDomainError.set(this.graphDomainPaginationError(
            decision.reason,
            this.graphDomains().length,
            this.graphDomainTotal(),
          ));
          return EMPTY;
        }
        return this.service.getCodeCompassGraphInventory(connectionId, decision.cursor);
      }),
    ).subscribe({
      next: page => {
        if (
          !this.graphDomainInventoryOperation.isCurrent(generation)
          || this.selectedConnectionId() !== connectionId
        ) return;
        if (
          expectedRevision
          && this.codeGraphRevision()
          && expectedRevision !== this.codeGraphRevision()
        ) {
          return;
        }
        const existingRevision = this.graphInventoryRevision();
        if (
          (expectedRevision && page.graphRevision !== expectedRevision)
          || (existingRevision && page.graphRevision !== existingRevision)
        ) {
          paginationInvalid = true;
          this.handleGraphRevisionMismatch(connectionId, expectedRevision, page.graphRevision);
          return;
        }
        const combined = [...this.graphDomains(), ...page.domains];
        const byKey = new Map(combined.map(domain => [domain.key, domain]));
        this.graphDomains.set([...byKey.values()]);
        this.graphDomainNextCursor.set(page.nextCursor);
        this.graphDomainTotal.set(page.totalDomains);
        this.graphInventoryNodeTotal.set(page.totalNodes);
        this.graphInventoryEdgeTotal.set(page.totalEdges);
        this.graphInventoryRevision.set(page.graphRevision);
      },
      error: () => {
        if (
          !this.graphDomainInventoryOperation.isCurrent(generation)
          || this.selectedConnectionId() !== connectionId
        ) return;
        this.pendingInventoryRevision = '';
        this.graphDomainLoading.set(false);
        this.inventoryLoaded = false;
        this.graphDomainNextCursor.set(null);
        this.graphDomainError.set(
          this.graphDomains().length
            ? 'Weitere Domains konnten nicht geladen werden; der bereits bestätigte Inventarstand bleibt erhalten.'
            : 'Domain-Inventar konnte nicht vertragskonform geladen werden.',
        );
      },
      complete: () => {
        if (
          !this.graphDomainInventoryOperation.isCurrent(generation)
          || this.selectedConnectionId() !== connectionId
        ) return;
        this.pendingInventoryRevision = '';
        this.graphDomainLoading.set(false);
        this.inventoryLoaded = paginationCompleted && !paginationInvalid;
        if (this.inventoryLoaded) {
          this.revisionRecoveryAttempted = false;
        }
      },
    });
    this.graphDomainInventoryOperation.replaceRequest(generation, request);
  }

  private ensureGraphDomainInventory(connectionId: string, graphRevision: string): void {
    if (this.selectedConnectionId() !== connectionId) return;
    if (
      this.inventoryLoaded
      && (!graphRevision || this.graphInventoryRevision() === graphRevision)
    ) {
      return;
    }
    if (this.graphDomainLoading() && this.pendingInventoryRevision === graphRevision) return;
    this.loadGraphDomainInventory(graphRevision);
  }

  private graphDomainPaginationError(
    reason: GraphDomainCursorInvalidReason,
    loaded: number,
    total: number,
  ): string {
    const cause = reason === 'cursor_repeated'
      ? 'der Server einen bereits verwendeten Cursor wiederholt hat'
      : reason === 'cursor_without_progress'
        ? 'eine weitere Seite keine neuen Bereiche geliefert hat'
        : 'Seitencursor und Gesamtzahl nicht zusammenpassen';
    return `Domain-Inventar unvollständig: ${cause} (${loaded} / ${total} Bereiche geladen).`;
  }

  private handleGraphRevisionMismatch(
    connectionId: string,
    expectedRevision: string,
    inventoryRevision: string,
  ): void {
    if (this.selectedConnectionId() !== connectionId) return;
    this.clearGraphInventoryState();
    this.graphDomainError.set(
      `Der Index wurde während des Ladens aktualisiert (${expectedRevision || 'unbekannt'} → ${inventoryRevision}).`,
    );
    if (this.revisionRecoveryAttempted) return;
    this.revisionRecoveryAttempted = true;
    this.loadGraph(true);
  }

  private loadReadyDomains(indexId: string, status: any): void {
    for (const mode of ['hubs', 'categories', 'clusters'] as const) {
      if (status?.[mode]?.status === 'ready') {
        this.loadReadyDomain(indexId, mode);
      }
    }
  }

  private loadReadyDomain(indexId: string, mode: string): void {
    const operation = this.operationFor(this.wikiReadyDomainOperations, mode);
    const generation = operation.restart();
    const subscription = this.service.getWikiDomains(indexId, mode).subscribe(domains => {
      if (operation.isCurrent(generation) && this.wikiIndexIsCurrent(indexId)) {
        this.setWikiDomains(mode, domains);
      }
    });
    operation.replaceRequest(generation, subscription);
  }

  private loadWikiDomainStatus(indexId: string): void {
    const generation = this.wikiDomainStatusBootstrapOperation.restart();
    const subscription = this.service.getWikiDomainStatus(indexId).subscribe(domainStatus => {
      if (
        !this.wikiDomainStatusBootstrapOperation.isCurrent(generation)
        || !this.wikiIndexIsCurrent(indexId)
      ) return;
      this.domainStatus.set(domainStatus);
      this.loadReadyDomains(indexId, domainStatus);
    });
    this.wikiDomainStatusBootstrapOperation.replaceRequest(generation, subscription);
  }

  private pollStatus(indexId: string): void {
    const generation = this.wikiStatusOperation.restart();
    const poll = () => {
      if (
        !this.wikiStatusOperation.isCurrent(generation)
        || !this.wikiIndexIsCurrent(indexId)
      ) return;
      const subscription = this.service.getWikiGraphStatus(indexId).subscribe(status => {
        if (
          !this.wikiStatusOperation.isCurrent(generation)
          || !this.wikiIndexIsCurrent(indexId)
        ) return;
        this.status.set(status);
        if (status?.status === 'building') {
          this.wikiStatusOperation.schedule(generation, poll, 5000);
        }
      });
      this.wikiStatusOperation.replaceRequest(generation, subscription);
    };
    this.wikiStatusOperation.schedule(generation, poll, 3000);
  }

  private pollDomainStatus(indexId: string, mode: string): void {
    const operation = this.operationFor(this.wikiDomainPollOperations, mode);
    const generation = operation.restart();
    const poll = () => {
      if (!operation.isCurrent(generation) || !this.wikiIndexIsCurrent(indexId)) return;
      const subscription = this.service.getWikiDomainStatus(indexId).subscribe(status => {
        if (!operation.isCurrent(generation) || !this.wikiIndexIsCurrent(indexId)) return;
        this.domainStatus.update(current => ({
          ...(current ?? {}),
          [mode]: status?.[mode] ?? { status: 'not_built' },
        }));
        if (status?.[mode]?.status === 'building') {
          operation.schedule(generation, poll, 5000);
        } else if (status?.[mode]?.status === 'ready') {
          this.loadReadyDomain(indexId, mode);
        }
      });
      operation.replaceRequest(generation, subscription);
    };
    operation.schedule(generation, poll, 3000);
  }

  private resetViewState(): void {
    this.metadata.set(null);
    this.rawGraph.set(null);
    this.graphMode.set('code');
    this.loading.set(false);
    this.error.set('');
    this.status.set(null);
    this.searchResults.set([]);
    this.searchQuery.set('');
    this.expandedSlug.set('');
    this.domainStatus.set(null);
    this.hubDomains.set([]);
    this.categoryDomains.set([]);
    this.clusterDomains.set([]);
    this.selectedGraphDomain.set('');
    this.includeGraphSubdomains.set(true);
    this.confirmedNodeLimit.set(0);
    this.codeGraphRevision.set('');
    this.codeGraphEvidenceRevision.set('');
    this.revisionRecoveryAttempted = false;
    this.graphSubscription?.unsubscribe();
    this.cancelFullGraphLoad(false);
    this.clearGraphInventoryState();
    this.cancelWikiContext();
  }

  private clearGraphInventoryState(cancelRequest = true): void {
    if (cancelRequest) this.graphDomainInventoryOperation.cancel();
    this.graphDomains.set([]);
    this.graphDomainNextCursor.set(null);
    this.graphDomainTotal.set(0);
    this.graphInventoryNodeTotal.set(0);
    this.graphInventoryEdgeTotal.set(0);
    this.graphInventoryRevision.set('');
    this.graphDomainLoading.set(false);
    this.graphDomainError.set('');
    this.pendingInventoryRevision = '';
    this.inventoryLoaded = false;
  }

  private cancelWikiContext(): void {
    this.wikiSubscriptions.unsubscribe();
    this.wikiSubscriptions = new Subscription();
    this.initializedWikiIndexId = '';
    this.wikiStatusOperation.cancel();
    this.wikiDomainStatusBootstrapOperation.cancel();
    this.wikiBuildOperation.cancel();
    this.cancelWikiOperationMap(this.wikiDomainBuildOperations);
    this.cancelWikiOperationMap(this.wikiDomainPollOperations);
    this.cancelWikiOperationMap(this.wikiReadyDomainOperations);
  }

  private wikiIndexIsCurrent(indexId: string): boolean {
    return this.initializedWikiIndexId === indexId
      && this.selectedKnowledgeIndexId() === indexId;
  }

  private operationFor(
    operations: Map<string, WikiOperationSlot>,
    key: string,
  ): WikiOperationSlot {
    const existing = operations.get(key);
    if (existing) return existing;
    const operation = new WikiOperationSlot();
    operations.set(key, operation);
    return operation;
  }

  private cancelWikiOperationMap(operations: Map<string, WikiOperationSlot>): void {
    operations.forEach(operation => operation.cancel());
    operations.clear();
  }

  private setWikiDomains(mode: string, domains: any[]): void {
    switch (mode) {
      case 'hubs':
        this.hubDomains.set(domains);
        break;
      case 'categories':
        this.categoryDomains.set(domains);
        break;
      case 'clusters':
        this.clusterDomains.set(domains);
        break;
    }
  }

  private graphRequestIsCurrent(
    connectionId: string,
    domainScope: string,
    includeSubdomains: boolean,
  ): boolean {
    return this.selectedConnectionId() === connectionId
      && this.selectedGraphDomain() === domainScope
      && this.includeGraphSubdomains() === includeSubdomains;
  }

  private expectedFullGraphNodeTotal(domainScope: string): number {
    if (!domainScope) return this.graphInventoryNodeTotal() || this.globalNodeTotal();
    const domain = this.graphDomains().find(candidate => candidate.key === domainScope);
    if (!domain) return 0;
    if (this.includeGraphSubdomains()) {
      return domain.semanticScopeStatus === 'available'
        ? domain.completeNodeCount ?? domain.subtreeNodeCount
        : domain.subtreeNodeCount;
    }
    return domain.directNodeCount;
  }

  private fullGraphLoadErrorMessage(error: unknown): string {
    if (!(error instanceof CodeCompassFullGraphLoadError)) {
      return 'Der vollständige Graph konnte nicht vertragskonform geladen werden.';
    }
    if (
      error.reason === 'revision_changed'
      || error.reason === 'evidence_revision_changed'
    ) {
      return 'Der Index wurde während des vollständigen Ladens aktualisiert. Der alte Datenstrom wurde verworfen; bitte den aktuellen Indexstand laden und die Domain erneut auswählen.';
    }
    if (
      error.reason === 'scope_changed'
      || error.reason === 'semantic_scope_changed'
      || error.reason === 'source_changed'
    ) {
      return 'Der vollständige Datenstrom wurde wegen eines Source-/Scope-Wechsels verworfen.';
    }
    if (error.reason === 'duplicate_record') {
      return 'Der vollständige Datenstrom enthielt überlappende Knoten- oder Kanten-IDs und wurde ohne Teilübernahme verworfen.';
    }
    return 'Der vollständige Datenstrom wurde wegen inkonsistenter Seitencursor abgebrochen; es wurden keine Teildaten übernommen.';
  }

  private resetFullGraphProgress(): void {
    this.fullGraphLoadState.set('idle');
    this.fullGraphLoadedNodes.set(0);
    this.fullGraphTotalNodes.set(0);
    this.fullGraphLoadedEdges.set(0);
    this.fullGraphTotalEdges.set(0);
    this.fullGraphSemanticScope.set(null);
  }

  private codeGraphContentRevision(graph: unknown): string {
    if (!graph || typeof graph !== 'object' || Array.isArray(graph)) return '';
    const metadata = (graph as { metadata?: unknown }).metadata;
    if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) return '';
    const values = metadata as Record<string, unknown>;
    for (const field of [
      'content_graph_revision',
      'evidence_graph_revision',
      'parent_graph_revision',
      'graph_revision',
    ]) {
      const value = values[field];
      if (typeof value === 'string' && value.trim()) return value.trim();
    }
    return '';
  }

  private stableGraphEvidenceRevision(graph: unknown): string {
    if (!graph || typeof graph !== 'object' || Array.isArray(graph)) return '';
    const metadata = (graph as { metadata?: unknown }).metadata;
    if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) return '';
    const values = metadata as Record<string, unknown>;
    for (const field of ['evidence_graph_revision', 'parent_graph_revision']) {
      const value = values[field];
      if (typeof value === 'string' && value.trim()) return value.trim();
    }
    return '';
  }

  private graphDomainSourceLabel(source: string): string {
    switch (source) {
      case 'domain_id': return 'deklarierte Domain';
      case 'domain_path': return 'Domainpfad';
      case 'path': return 'Repositorypfad';
      case 'unassigned': return 'nicht zugeordnet';
      default: return source;
    }
  }

  private activeLoadStrategy(): GraphLoadStrategy {
    return GRAPH_LOAD_STRATEGIES.find(
      strategy => strategy.id === this.graphLoadStrategy(),
    ) ?? GRAPH_LOAD_STRATEGIES[0]!;
  }

  private edgeLimitFor(nodeLimit: number): number {
    return Math.min(MAX_GRAPH_PREVIEW_EDGES, Math.max(1, nodeLimit * 4));
  }

  private graphItemCount(primary: string, legacy: string): number {
    const graph = this.rawGraph();
    const values = graph?.[primary] ?? graph?.[legacy];
    return Array.isArray(values) ? values.length : 0;
  }

  private metadataCount(...fields: string[]): number {
    return this.metadataCountOrNull(...fields) ?? 0;
  }

  private metadataCountOrNull(...fields: string[]): number | null {
    const metadata = this.metadata();
    for (const field of fields) {
      const value = this.metadataCountFrom(metadata, field);
      if (value !== null) return value;
    }
    return null;
  }

  private metadataCountFrom(
    metadata: Record<string, unknown> | null | undefined,
    field: string,
  ): number | null {
    const value = metadata?.[field];
    return typeof value === 'number'
      && Number.isFinite(value)
      && Number.isInteger(value)
      && value >= 0
      ? value
      : null;
  }
}
