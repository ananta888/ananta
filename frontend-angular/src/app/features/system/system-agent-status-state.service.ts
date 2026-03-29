import { Injectable, OnDestroy, inject, signal } from '@angular/core';
import { Subscription, interval } from 'rxjs';

import { HubApiService } from '../../services/hub-api.service';

@Injectable({ providedIn: 'root' })
export class SystemAgentStatusStateService implements OnDestroy {
  private hubApi = inject(HubApiService);

  readonly statuses = signal<Record<string, string>>({});
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

  connect(hubUrl: string | undefined | null, pollMs = 30000): void {
    const normalizedHubUrl = String(hubUrl || '').trim();
    if (!normalizedHubUrl) return;

    if (this.hubUrl && this.hubUrl !== normalizedHubUrl) {
      this.stopPolling();
      this.statuses.set({});
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
    this.hubApi.listAgents(this.hubUrl).subscribe({
      next: (agentsResponse: any) => {
        const nextStatuses: Record<string, string> = {};
        if (Array.isArray(agentsResponse)) {
          for (const entry of agentsResponse) {
            if (entry?.name) nextStatuses[String(entry.name)] = String(entry.status || 'offline');
          }
        } else if (agentsResponse && typeof agentsResponse === 'object') {
          for (const [name, value] of Object.entries(agentsResponse)) {
            const status = (value as any)?.status;
            if (typeof status === 'string' && name) nextStatuses[String(name)] = status;
          }
        }
        this.statuses.set(nextStatuses);
        this.lastLoadedAt.set(Math.floor(Date.now() / 1000));
        this.error.set(null);
      },
      error: () => {
        this.error.set('Agentenstatus konnte nicht geladen werden');
        this.loading.set(false);
        this.inFlight = false;
      },
      complete: () => {
        this.loading.set(false);
        this.inFlight = false;
      },
    });
  }

  statusFor(agentName: string): string | null {
    return this.statuses()[String(agentName || '')] || null;
  }

  private startPolling(pollMs: number): void {
    if (this.pollSub) return;
    this.pollSub = interval(Math.max(5000, pollMs || 30000)).subscribe(() => this.reload());
  }

  private stopPolling(): void {
    this.pollSub?.unsubscribe();
    this.pollSub = undefined;
  }
}
