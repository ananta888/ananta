import { Injectable, OnDestroy, inject, signal } from '@angular/core';
import { Subscription, interval } from 'rxjs';

import { UiAsyncState, buildUiAsyncState, isCollectionEmpty } from '../models/ui-async-state';
import { HubApiService } from './hub-api.service';

@Injectable({ providedIn: 'root' })
export class HubTaskCollectionStateService implements OnDestroy {
  private api = inject(HubApiService);

  readonly tasks = signal<any[]>([]);
  readonly loading = signal(false);
  readonly lastLoadedAt = signal<number | null>(null);
  readonly error = signal<string | null>(null);

  private hubUrl?: string;
  private pollSub?: Subscription;
  private inFlight = false;
  private connectionCount = 0;

  ngOnDestroy(): void {
    this.stopPolling();
  }

  connect(hubUrl: string | undefined | null, pollMs = 10000): void {
    const normalizedHubUrl = String(hubUrl || '').trim();
    if (!normalizedHubUrl) return;

    if (this.hubUrl && this.hubUrl !== normalizedHubUrl) {
      this.stopPolling();
      this.tasks.set([]);
      this.lastLoadedAt.set(null);
      this.error.set(null);
      this.connectionCount = 0;
    }

    this.hubUrl = normalizedHubUrl;
    this.connectionCount += 1;
    this.startPolling(pollMs);
    this.reload();
  }

  disconnect(hubUrl?: string | null): void {
    const normalizedHubUrl = String(hubUrl || '').trim();
    if (normalizedHubUrl && this.hubUrl && normalizedHubUrl !== this.hubUrl) return;

    this.connectionCount = Math.max(0, this.connectionCount - 1);
    if (this.connectionCount === 0) {
      this.stopPolling();
    }
  }

  reload(): void {
    if (!this.hubUrl || this.inFlight) return;

    this.inFlight = true;
    this.loading.set(true);
    this.api.listTasks(this.hubUrl).subscribe({
      next: (tasks) => {
        this.tasks.set(Array.isArray(tasks) ? tasks : []);
        this.lastLoadedAt.set(Math.floor(Date.now() / 1000));
        this.error.set(null);
      },
      error: () => {
        this.error.set('Tasks konnten nicht geladen werden');
        this.inFlight = false;
        this.loading.set(false);
      },
      complete: () => {
        this.inFlight = false;
        this.loading.set(false);
      },
    });
  }

  childrenOf(taskId: string): any[] {
    return this.tasks().filter((task: any) => task?.parent_task_id === taskId);
  }

  snapshot(): {
    tasks: any[];
    loading: boolean;
    refreshing: boolean;
    empty: boolean;
    lastLoadedAt: number | null;
    error: string | null;
    asyncState: UiAsyncState<any[]>;
    counts: Record<string, number>;
  } {
    const tasks = this.tasks();
    const loading = this.loading();
    const lastLoadedAt = this.lastLoadedAt();
    const error = this.error();
    return {
      tasks,
      loading,
      refreshing: loading && lastLoadedAt !== null,
      empty: isCollectionEmpty(tasks) && !loading && !error,
      lastLoadedAt,
      error,
      asyncState: buildUiAsyncState(tasks, {
        loading,
        refreshing: loading && lastLoadedAt !== null,
        empty: isCollectionEmpty(tasks) && !loading && !error,
        error,
        lastLoadedAt,
      }),
      counts: tasks.reduce((acc: Record<string, number>, task: any) => {
        const status = String(task?.status || 'unknown');
        acc[status] = (acc[status] || 0) + 1;
        return acc;
      }, {}),
    };
  }

  private startPolling(pollMs: number): void {
    if (this.pollSub) return;
    this.pollSub = interval(Math.max(3000, pollMs || 10000)).subscribe(() => this.reload());
  }

  private stopPolling(): void {
    this.pollSub?.unsubscribe();
    this.pollSub = undefined;
  }
}
