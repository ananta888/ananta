import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { Subject, Subscription } from 'rxjs';
import { debounceTime, distinctUntilChanged } from 'rxjs/operators';

import { GraphViewerComponent } from '../../codecompass-graph/components/graph-viewer/graph-viewer.component';
import { InternalsService } from '../services/internals.service';

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
  private readonly searchRequests = new Subject<string>();
  private searchSubscription: Subscription | null = null;
  private domainPollTimer: ReturnType<typeof setTimeout> | null = null;

  readonly indexes = signal<any[]>([]);
  readonly selectedConnectionId = signal('');
  readonly rawGraph = signal<any>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly metadata = signal<Record<string, unknown> | null>(null);
  readonly selectedIndex = computed(
    () => this.indexes().find(index => index.id === this.selectedConnectionId()) ?? null,
  );
  readonly selectedKnowledgeIndexId = computed(
    () => String(this.selectedIndex()?.knowledge_index_id ?? '').trim(),
  );

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
    this.service.listKnowledgeIndexes().subscribe({
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
    this.searchSubscription?.unsubscribe();
    if (this.domainPollTimer !== null) clearTimeout(this.domainPollTimer);
  }

  loadGraph(): void {
    const connectionId = this.selectedConnectionId();
    if (!connectionId) {
      this.error.set('Keine projektgebundene Connection ausgewählt');
      return;
    }
    this.loading.set(true);
    this.error.set('');
    this.rawGraph.set(null);
    this.service.getCodeCompassGraph(connectionId).subscribe({
      next: graph => {
        if (this.selectedConnectionId() !== connectionId) return;
        this.loading.set(false);
        if (!graph) return this.error.set('Quellgraph nicht verfügbar');
        this.rawGraph.set(graph);
        this.metadata.set(graph?.metadata ?? null);
        const nodeCount = Number(
          graph?.metadata?.node_count ?? graph?.nodes?.length ?? 0,
        );
        if (!Number.isFinite(nodeCount) || nodeCount <= 0) {
          this.error.set(
            'Der aktive Index enthält keinen Quellgraphen. Quelle mit dem Profil "Deep Code" neu indexieren.',
          );
          return;
        }
        const knowledgeIndexId = this.selectedKnowledgeIndexId();
        if (knowledgeIndexId) this.initializeWiki(knowledgeIndexId);
      },
      error: () => {
        if (this.selectedConnectionId() === connectionId) {
          this.loading.set(false);
          this.error.set('Fehler beim Laden des projektgebundenen Quellgraphen');
        }
      },
    });
  }

  changeSource(value: string): void {
    this.selectedConnectionId.set(value);
    this.resetViewState();
    this.loadGraph();
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
    const indexId = this.selectedKnowledgeIndexId();
    if (!indexId) return;
    this.expandedSlug.set(slug);
    this.searchResults.set([]);
    this.searchQuery.set('');
    this.loading.set(true);
    this.error.set('');
    this.service.expandWikiArticle(indexId, slug).subscribe({
      next: graph => {
        if (this.selectedKnowledgeIndexId() !== indexId) return;
        this.loading.set(false);
        if (!graph?.nodes?.length) return this.error.set('Keine Nachbarn gefunden');
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
    this.loading.set(true);
    this.error.set('');
    this.service.getWikiDomainGraph(indexId, mode, domainId).subscribe({
      next: graph => {
        if (this.selectedKnowledgeIndexId() !== indexId) return;
        this.loading.set(false);
        if (!graph?.nodes?.length) return this.error.set('Keine Artikel in dieser Domäne');
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
    this.service.triggerWikiGraphBuild(indexId, force).subscribe(() => this.pollStatus(indexId));
  }

  buildDomain(mode: string): void {
    const indexId = this.selectedKnowledgeIndexId();
    if (!indexId) return;
    this.domainStatus.update(current => ({ ...(current ?? {}), [mode]: { status: 'building' } }));
    this.service.buildWikiDomains(indexId, mode).subscribe(() => this.pollDomainStatus(indexId, mode));
  }

  domainModeStatus(mode: string): string {
    return this.domainStatus()?.[mode]?.status ?? 'not_built';
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
