import { signal } from '@angular/core';

import type { InternalsService } from '../services/internals.service';
import { WikiOperationSlot } from './codehug-wiki-graph.support';

type WikiBuildApi = Pick<
  InternalsService,
  'getWikiGraphStatus' | 'triggerWikiGraphBuild' | 'getWikiDomainStatus' | 'buildWikiDomains' | 'getWikiDomains'
>;

/** Component context the wiki build status reads or reports to. */
export interface WikiBuildStatusContext {
  /** True while indexId is both the initialized and the selected wiki index. */
  isCurrentIndex(indexId: string): boolean;
  reportError(message: string): void;
}

/**
 * Wiki graph and wiki domain build status of the selected knowledge index:
 * initial status, build triggers, bounded status polling and the ready
 * domain lists (hubs, categories, clusters). Every request and timer is
 * fenced by its own operation slot and by the current index.
 */
export class CodehugWikiBuildStatus {
  readonly status = signal<any>(null);
  readonly domainStatus = signal<any>(null);
  readonly hubDomains = signal<any[]>([]);
  readonly categoryDomains = signal<any[]>([]);
  readonly clusterDomains = signal<any[]>([]);

  private readonly statusOperation = new WikiOperationSlot();
  private readonly domainStatusBootstrapOperation = new WikiOperationSlot();
  private readonly buildOperation = new WikiOperationSlot();
  private readonly domainBuildOperations = new Map<string, WikiOperationSlot>();
  private readonly domainPollOperations = new Map<string, WikiOperationSlot>();
  private readonly readyDomainOperations = new Map<string, WikiOperationSlot>();

  constructor(
    private readonly api: WikiBuildApi,
    private readonly context: WikiBuildStatusContext,
  ) {}

  /** Loads the build status of a newly initialized index. */
  loadStatus(indexId: string): void {
    const generation = this.statusOperation.restart();
    const statusSubscription = this.api.getWikiGraphStatus(indexId).subscribe(status => {
      if (
        !this.statusOperation.isCurrent(generation)
        || !this.context.isCurrentIndex(indexId)
      ) return;
      this.status.set(status);
      if (status?.status === 'ready') {
        this.loadDomainStatus(indexId);
      }
    });
    this.statusOperation.replaceRequest(generation, statusSubscription);
  }

  build(indexId: string, force: boolean): void {
    this.status.set({ status: 'building' });
    this.statusOperation.cancel();
    const generation = this.buildOperation.restart();
    const subscription = this.api.triggerWikiGraphBuild(indexId, force).subscribe({
      next: () => {
        if (
          this.buildOperation.isCurrent(generation)
          && this.context.isCurrentIndex(indexId)
        ) {
          this.pollStatus(indexId);
        }
      },
      error: () => {
        if (
          this.buildOperation.isCurrent(generation)
          && this.context.isCurrentIndex(indexId)
        ) {
          this.status.set({ status: 'error' });
          this.context.reportError('Wiki-Graph-Build konnte nicht gestartet werden');
        }
      },
    });
    this.buildOperation.replaceRequest(generation, subscription);
  }

  buildDomain(indexId: string, mode: string): void {
    this.domainStatus.update(current => ({ ...(current ?? {}), [mode]: { status: 'building' } }));
    this.domainStatusBootstrapOperation.cancel();
    this.operationFor(this.readyDomainOperations, mode).cancel();
    this.operationFor(this.domainPollOperations, mode).cancel();
    const operation = this.operationFor(this.domainBuildOperations, mode);
    const generation = operation.restart();
    const subscription = this.api.buildWikiDomains(indexId, mode).subscribe({
      next: () => {
        if (operation.isCurrent(generation) && this.context.isCurrentIndex(indexId)) {
          this.pollDomainStatus(indexId, mode);
        }
      },
      error: () => {
        if (operation.isCurrent(generation) && this.context.isCurrentIndex(indexId)) {
          this.domainStatus.update(current => ({
            ...(current ?? {}),
            [mode]: { status: 'error' },
          }));
          this.context.reportError('Domain-Build konnte nicht gestartet werden');
        }
      },
    });
    operation.replaceRequest(generation, subscription);
  }

  domainModeStatus(mode: string): string {
    return this.domainStatus()?.[mode]?.status ?? 'not_built';
  }

  /** Cancels every request and timer; later responses are ignored. */
  cancel(): void {
    this.statusOperation.cancel();
    this.domainStatusBootstrapOperation.cancel();
    this.buildOperation.cancel();
    this.cancelOperationMap(this.domainBuildOperations);
    this.cancelOperationMap(this.domainPollOperations);
    this.cancelOperationMap(this.readyDomainOperations);
  }

  private loadReadyDomains(indexId: string, status: any): void {
    for (const mode of ['hubs', 'categories', 'clusters'] as const) {
      if (status?.[mode]?.status === 'ready') {
        this.loadReadyDomain(indexId, mode);
      }
    }
  }

  private loadReadyDomain(indexId: string, mode: string): void {
    const operation = this.operationFor(this.readyDomainOperations, mode);
    const generation = operation.restart();
    const subscription = this.api.getWikiDomains(indexId, mode).subscribe(domains => {
      if (operation.isCurrent(generation) && this.context.isCurrentIndex(indexId)) {
        this.setDomains(mode, domains);
      }
    });
    operation.replaceRequest(generation, subscription);
  }

  private loadDomainStatus(indexId: string): void {
    const generation = this.domainStatusBootstrapOperation.restart();
    const subscription = this.api.getWikiDomainStatus(indexId).subscribe(domainStatus => {
      if (
        !this.domainStatusBootstrapOperation.isCurrent(generation)
        || !this.context.isCurrentIndex(indexId)
      ) return;
      this.domainStatus.set(domainStatus);
      this.loadReadyDomains(indexId, domainStatus);
    });
    this.domainStatusBootstrapOperation.replaceRequest(generation, subscription);
  }

  private pollStatus(indexId: string): void {
    const generation = this.statusOperation.restart();
    const poll = () => {
      if (
        !this.statusOperation.isCurrent(generation)
        || !this.context.isCurrentIndex(indexId)
      ) return;
      const subscription = this.api.getWikiGraphStatus(indexId).subscribe(status => {
        if (
          !this.statusOperation.isCurrent(generation)
          || !this.context.isCurrentIndex(indexId)
        ) return;
        this.status.set(status);
        if (status?.status === 'building') {
          this.statusOperation.schedule(generation, poll, 5000);
        }
      });
      this.statusOperation.replaceRequest(generation, subscription);
    };
    this.statusOperation.schedule(generation, poll, 3000);
  }

  private pollDomainStatus(indexId: string, mode: string): void {
    const operation = this.operationFor(this.domainPollOperations, mode);
    const generation = operation.restart();
    const poll = () => {
      if (!operation.isCurrent(generation) || !this.context.isCurrentIndex(indexId)) return;
      const subscription = this.api.getWikiDomainStatus(indexId).subscribe(status => {
        if (!operation.isCurrent(generation) || !this.context.isCurrentIndex(indexId)) return;
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

  private cancelOperationMap(operations: Map<string, WikiOperationSlot>): void {
    operations.forEach(operation => operation.cancel());
    operations.clear();
  }

  private setDomains(mode: string, domains: any[]): void {
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
}
