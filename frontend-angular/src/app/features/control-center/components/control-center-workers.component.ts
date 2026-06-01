import { Component } from '@angular/core';
import { NgFor } from '@angular/common';
import { StatusChipComponent } from './status-chip.component';

interface WorkerRow {
  id: string;
  runtime: 'local' | 'docker' | 'remote' | 'cloud';
  health: 'online' | 'degraded' | 'offline';
  capabilities: string[];
  localOnly: boolean;
}

@Component({
  standalone: true,
  selector: 'app-control-center-workers',
  imports: [NgFor, StatusChipComponent],
  template: `
    <h2>Workers</h2>
    <div class="grid">
      <article class="card" *ngFor="let w of workers">
        <header>
          <strong>{{ w.id }}</strong>
          <app-status-chip [label]="w.health" [tone]="tone(w.health)" />
        </header>
        <p class="muted">Runtime: {{ w.runtime }} · Boundary: {{ w.localOnly ? 'local-only' : 'cloud-allowed' }}</p>
        <p><strong>Capabilities</strong></p>
        <ul><li *ngFor="let c of w.capabilities">{{ c }}</li></ul>
      </article>
    </div>

    <h3>Capability Matrix</h3>
    <table class="matrix">
      <thead><tr><th>Worker</th><th>Tools</th><th>FS Access</th><th>Terminal</th><th>Boundary</th></tr></thead>
      <tbody>
        <tr *ngFor="let w of workers">
          <td>{{ w.id }}</td>
          <td>{{ w.capabilities.join(', ') }}</td>
          <td>{{ w.capabilities.includes('fs') ? 'yes' : 'no' }}</td>
          <td>{{ w.capabilities.includes('terminal') ? 'yes' : 'no' }}</td>
          <td>{{ w.localOnly ? 'local-only' : 'cloud-allowed' }}</td>
        </tr>
      </tbody>
    </table>
  `,
  styles: [`.grid{display:grid;grid-template-columns:repeat(2,minmax(260px,1fr));gap:10px}.card{border:1px solid #1f2937;border-radius:10px;padding:10px;background:#0f172a}header{display:flex;justify-content:space-between}.muted{color:#94a3b8;font-size:12px}.matrix{width:100%;border-collapse:collapse;margin-top:8px}.matrix th,.matrix td{border:1px solid #1f2937;padding:6px}.matrix th{background:#111827}@media (max-width:900px){.grid{grid-template-columns:1fr}}`]
})
export class ControlCenterWorkersComponent {
  workers: WorkerRow[] = [
    { id: 'alpha', runtime: 'docker', health: 'online', capabilities: ['fs', 'terminal', 'tool_exec'], localOnly: true },
    { id: 'beta', runtime: 'remote', health: 'degraded', capabilities: ['fs', 'tool_exec'], localOnly: false },
    { id: 'gamma', runtime: 'local', health: 'offline', capabilities: ['tool_exec'], localOnly: true },
  ];

  tone(s: WorkerRow['health']): 'neutral'|'ok'|'warn'|'danger'|'info' {
    if (s === 'online') return 'ok';
    if (s === 'degraded') return 'warn';
    return 'danger';
  }
}
