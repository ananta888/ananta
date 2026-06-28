import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { Subject, Subscription } from 'rxjs';
import { debounceTime, distinctUntilChanged } from 'rxjs/operators';

import { GraphViewerComponent } from '../../codecompass-graph/components/graph-viewer/graph-viewer.component';
import { InternalsService } from '../services/internals.service';

@Component({
  selector: 'codehug-wiki-graph',
  standalone: true,
  imports: [GraphViewerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './codehug-wiki-graph.component.html',
  styleUrls: ['./codehug-internals.component.scss'],
})
export class CodehugWikiGraphComponent implements OnInit, OnDestroy {
  private readonly service = inject(InternalsService);
  private readonly searchRequests = new Subject<string>();
  private searchSubscription: Subscription | null = null;
  private domainPollTimer: ReturnType<typeof setTimeout> | null = null;

  readonly indexes = signal<any[]>([]);
  readonly graphMode = signal('self');
  readonly rawGraph = signal<any>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly domains = signal<Array<{ domain: string; display_name: string; file_count: number; kind: string; depth?: number }>>([]);
  readonly domain = signal('agent.routes');
  readonly detailLevel = signal(2);
  readonly graphDepth = signal(0);
  readonly maxNodes = signal(0);
  readonly maxEdges = signal(0);
  readonly metadata = signal<Record<string, unknown> | null>(null);
  readonly selectedIndex = computed(() => this.indexes().find(index => index.id === this.graphMode()) ?? null);

  readonly status = signal<any>(null);
  readonly searchQuery = signal('');
  readonly searchResults = signal<Array<{ slug: string; title: string }>>([]);
  readonly expandedSlug = signal('');
  readonly domainStatus = signal<any>(null);
  readonly hubDomains = signal<any[]>([]);
  readonly categoryDomains = signal<any[]>([]);
  readonly clusterDomains = signal<any[]>([]);

  ngOnInit(): void {
    this.service.getSelfGraphDomains().subscribe(domains => {
      this.domains.set(domains);
      const preferred = domains.find(item => item.domain === 'agent.routes')
        ?? domains.find(item => item.domain === 'agent')
        ?? domains.find(item => item.file_count > 0)
        ?? domains[0];
      if (preferred) this.domain.set(preferred.domain);
      this.loadSelfGraph();
    });
    this.service.listKnowledgeIndexes().subscribe(indexes => this.indexes.set(indexes));
  }

  ngOnDestroy(): void {
    this.searchSubscription?.unsubscribe();
    if (this.domainPollTimer !== null) clearTimeout(this.domainPollTimer);
  }

  loadSelfGraph(): void {
    this.loading.set(true);
    this.error.set('');
    this.rawGraph.set(null);
    this.service.getSelfGraph(
      this.domain(), this.detailLevel(), this.graphDepth(), this.maxNodes(), this.maxEdges(),
    ).subscribe({
      next: graph => {
        if (this.graphMode() !== 'self') return;
        this.loading.set(false);
        if (!graph) return this.error.set('Self-Graph nicht verfügbar');
        this.rawGraph.set(graph);
        this.metadata.set(graph?.metadata ?? null);
      },
      error: () => {
        if (this.graphMode() === 'self') {
          this.loading.set(false);
          this.error.set('Fehler beim Laden des Self-Graphs');
        }
      },
    });
  }

  changeSource(value: string): void {
    this.graphMode.set(value);
    this.resetViewState();
    value === 'self' ? this.loadSelfGraph() : this.initializeWiki(value);
  }

  search(query: string): void {
    this.searchQuery.set(query);
    if (!query) {
      this.rawGraph.set(null);
      this.expandedSlug.set('');
    }
    this.searchRequests.next(query);
  }

  expand(slug: string): void {
    const indexId = this.graphMode();
    if (indexId === 'self') return;
    this.expandedSlug.set(slug);
    this.searchResults.set([]);
    this.searchQuery.set('');
    this.loading.set(true);
    this.error.set('');
    this.service.expandWikiArticle(indexId, slug).subscribe({
      next: graph => {
        if (this.graphMode() !== indexId) return;
        this.loading.set(false);
        if (!graph?.nodes?.length) return this.error.set('Keine Nachbarn gefunden');
        this.rawGraph.set(graph);
        this.metadata.set(graph.metadata ?? null);
      },
      error: () => {
        if (this.graphMode() === indexId) {
          this.loading.set(false);
          this.error.set('Fehler beim Laden');
        }
      },
    });
  }

  selectDomain(mode: string, domainId: string): void {
    if (!domainId || this.graphMode() === 'self') return;
    if (mode === 'hubs') return this.expand(domainId);
    const indexId = this.graphMode();
    this.loading.set(true);
    this.error.set('');
    this.service.getWikiDomainGraph(indexId, mode, domainId).subscribe({
      next: graph => {
        if (this.graphMode() !== indexId) return;
        this.loading.set(false);
        if (!graph?.nodes?.length) return this.error.set('Keine Artikel in dieser Domäne');
        this.rawGraph.set(graph);
        this.metadata.set(graph.metadata ?? null);
      },
      error: () => {
        if (this.graphMode() === indexId) {
          this.loading.set(false);
          this.error.set('Fehler beim Laden');
        }
      },
    });
  }

  build(force = false): void {
    const indexId = this.graphMode();
    if (indexId === 'self') return;
    this.status.set({ status: 'building' });
    this.service.triggerWikiGraphBuild(indexId, force).subscribe(() => this.pollStatus(indexId));
  }

  buildDomain(mode: string): void {
    const indexId = this.graphMode();
    if (indexId === 'self') return;
    this.domainStatus.update(current => ({ ...(current ?? {}), [mode]: { status: 'building' } }));
    this.service.buildWikiDomains(indexId, mode).subscribe(() => this.pollDomainStatus(indexId, mode));
  }

  domainModeStatus(mode: string): string {
    return this.domainStatus()?.[mode]?.status ?? 'not_built';
  }

  domainOptionLabel(item: { display_name: string; file_count: number; depth?: number }): string {
    const depth = Math.min(Math.max(item.depth ?? 0, 0), 4);
    return `${depth ? `${'--'.repeat(depth)} ` : ''}${item.display_name}${item.file_count ? ` (${item.file_count})` : ''}`;
  }

  indexLabel(index: any): string {
    const source = index?.index_metadata?.source_id ?? index?.collection_id ?? index?.id ?? '?';
    return `${index?.source_scope ?? 'Index'}: ${String(source).replace(/[-_]/g, ' ')}`;
  }

  private initializeWiki(indexId: string): void {
    this.searchSubscription?.unsubscribe();
    this.service.getWikiGraphStatus(indexId).subscribe(status => {
      this.status.set(status);
      if (status?.status === 'ready') {
        this.service.getWikiDomainStatus(indexId).subscribe(domainStatus => {
          this.domainStatus.set(domainStatus);
          this.loadReadyDomains(indexId, domainStatus);
        });
      }
    });
    this.searchSubscription = this.searchRequests.pipe(debounceTime(300), distinctUntilChanged()).subscribe(query => {
      if (!query) return this.searchResults.set([]);
      this.service.searchWikiArticles(indexId, query).subscribe(results => this.searchResults.set(results));
    });
  }

  private loadReadyDomains(indexId: string, status: any): void {
    for (const [mode, target] of [
      ['hubs', this.hubDomains],
      ['categories', this.categoryDomains],
      ['clusters', this.clusterDomains],
    ] as const) {
      if (status?.[mode]?.status === 'ready') {
        this.service.getWikiDomains(indexId, mode).subscribe(domains => target.set(domains));
      }
    }
  }

  private pollStatus(indexId: string): void {
    const poll = () => this.service.getWikiGraphStatus(indexId).subscribe(status => {
      this.status.set(status);
      if (status?.status === 'building') setTimeout(poll, 5000);
    });
    setTimeout(poll, 3000);
  }

  private pollDomainStatus(indexId: string, mode: string): void {
    if (this.domainPollTimer !== null) clearTimeout(this.domainPollTimer);
    const poll = () => this.service.getWikiDomainStatus(indexId).subscribe(status => {
      this.domainStatus.set(status);
      if (status?.[mode]?.status === 'building') {
        this.domainPollTimer = setTimeout(poll, 5000);
      } else if (status?.[mode]?.status === 'ready') {
        this.domainPollTimer = null;
        this.loadReadyDomains(indexId, status);
      }
    });
    this.domainPollTimer = setTimeout(poll, 3000);
  }

  private resetViewState(): void {
    this.metadata.set(null);
    this.rawGraph.set(null);
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
    if (this.domainPollTimer !== null) clearTimeout(this.domainPollTimer);
  }
}
