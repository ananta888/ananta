import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { HubApiService } from '../../services/hub-api.service';
import { NotificationService } from '../../services/notification.service';
import { UserAuthService } from '../../services/user-auth.service';

const ROLE_CHANGE_ACTIONS = new Set([
  'user_role_updated',
  'user_created',
  'user_deleted',
  'account_lockout',
  'ip_banned',
]);

@Component({
  standalone: true,
  selector: 'app-role-audit',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card">
      <div class="row flex-between">
        <h3>Rollenänderungen &amp; Benutzer-Audit</h3>
        <button class="button-outline" (click)="load()">🔄 Aktualisieren</button>
      </div>
      <p class="muted">Zeigt sicherheitsrelevante Benutzer- und Rollenereignisse aus dem Audit-Log.</p>

      <label class="label-block">
        Filter
        <input [(ngModel)]="filterText" placeholder="Benutzer, Aktion…" />
      </label>

      @if (loading) {
        <p class="muted">Lade…</p>
      } @else if (filteredEvents.length === 0) {
        <p class="muted">Keine Ereignisse gefunden.</p>
      } @else {
        <table>
          <thead>
            <tr>
              <th>Zeit</th>
              <th>Aktion</th>
              <th>Benutzer</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            @for (ev of filteredEvents; track ev) {
              <tr [class]="rowClass(ev.action)">
                <td style="white-space: nowrap;">{{ formatTime(ev.timestamp) }}</td>
                <td><code>{{ ev.action }}</code></td>
                <td>{{ ev.username || '—' }}</td>
                <td style="font-size: 12px; color: #666;">{{ summaryFor(ev) }}</td>
              </tr>
            }
          </tbody>
        </table>
      }
    </div>
  `,
  styles: [`
    tr.critical { background: rgba(220,53,69,0.08); }
    tr.warning  { background: rgba(255,193,7,0.08); }
  `],
})
export class RoleAuditComponent implements OnInit {
  private hubApi = inject(HubApiService);
  private dir = inject(AgentDirectoryService);
  private auth = inject(UserAuthService);
  private ns = inject(NotificationService);

  loading = true;
  filterText = '';
  private allEvents: any[] = [];

  get filteredEvents() {
    const q = this.filterText.toLowerCase();
    return this.allEvents.filter(ev =>
      !q
      || (ev.action || '').toLowerCase().includes(q)
      || (ev.username || '').toLowerCase().includes(q)
      || JSON.stringify(ev.details || {}).toLowerCase().includes(q)
    );
  }

  ngOnInit() { this.load(); }

  load() {
    this.loading = true;
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) {
      this.ns.error('Kein Hub gefunden');
      this.loading = false;
      return;
    }

    const token = this.auth.token ?? undefined;
    this.hubApi.getAuditLogs(hub.url, 500, 0, token).subscribe({
      next: (logs: any[]) => {
        this.allEvents = logs.filter(ev => ROLE_CHANGE_ACTIONS.has(ev.action));
        this.loading = false;
      },
      error: () => {
        this.ns.error('Audit-Logs konnten nicht geladen werden');
        this.loading = false;
      },
    });
  }

  formatTime(ts: string | number): string {
    if (!ts) return '—';
    return new Date(typeof ts === 'number' ? ts * 1000 : ts).toLocaleString('de-DE');
  }

  summaryFor(ev: any): string {
    const d = ev.details || {};
    if (ev.action === 'user_role_updated') {
      return `${d.target_user ?? '?'} → ${d.new_role ?? '?'}`;
    }
    if (ev.action === 'user_created') {
      return `${d.new_user ?? '?'} (${d.role ?? 'user'})`;
    }
    if (ev.action === 'user_deleted') {
      return `${d.deleted_user ?? '?'}`;
    }
    if (ev.action === 'account_lockout') {
      return `${d.username ?? '?'}`;
    }
    if (ev.action === 'ip_banned') {
      return `${d.ip ?? '?'} — ${d.reason ?? ''}`;
    }
    return JSON.stringify(d);
  }

  rowClass(action: string): string {
    if (['account_lockout', 'ip_banned', 'user_deleted'].includes(action)) return 'critical';
    if (action === 'user_role_updated') return 'warning';
    return '';
  }
}
