import { Component, inject } from '@angular/core';
import { JsonPipe } from '@angular/common';
import { WorkerPoolApiService } from '../services/worker-pool-api.service';
import { SystemFacade } from '../features/system/system.facade';

@Component({
  standalone: true,
  imports: [JsonPipe],
  selector: 'app-worker-pool-dashboard',
  template: `
    <h2>Worker Pool</h2>
    <p class="muted">Scheduler-Diagnostik fuer Worker/Ollama/Queues.</p>
    <div class="row" style="gap:8px; margin-bottom: 12px;">
      <button (click)="load()">Aktualisieren</button>
      <button class="button-outline" (click)="cleanup()">Stale Leases bereinigen</button>
    </div>
    <pre style="white-space: pre-wrap; word-break: break-word;">{{ status | json }}</pre>
  `,
})
export class WorkerPoolDashboardComponent {
  private api = inject(WorkerPoolApiService);
  private system = inject(SystemFacade);

  status: any = {};

  load(): void {
    const hub = this.system.resolveHubAgent();
    if (!hub?.url) return;
    this.api.getStatus(hub.url).subscribe({ next: (data) => (this.status = data || {}) });
  }

  cleanup(): void {
    const hub = this.system.resolveHubAgent();
    if (!hub?.url) return;
    this.api.cleanupStaleLeases(hub.url).subscribe({ next: () => this.load() });
  }
}
