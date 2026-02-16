import { ChangeDetectorRef, Component, NgZone, OnInit, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-audit-log',
  imports: [FormsModule],
  template: `
    <div class="card">
      <div class="row" style="justify-content: space-between; align-items: center;">
        <h3>Audit-Logs (Admin)</h3>
        <div class="row">
          <button (click)="analyzeLogs()" [disabled]="analyzing" class="button-outline" style="margin-right: 10px;">
            {{ analyzing ? '⏳ Analysiere...' : '🧠 KI-Analyse' }}
          </button>
          <button (click)="loadLogs()" class="button-outline">🔄 Aktualisieren</button>
        </div>
      </div>
      <p class="muted">Überblick über administrative Aktionen und Systemereignisse.</p>
    
      @if (analysisResult) {
        <div class="card" style="background: #f8f9fa; border-left: 4px solid #007bff; margin-bottom: 20px;">
          <div style="display: flex; justify-content: space-between;">
            <strong>KI-Sicherheitsanalyse:</strong>
            <button (click)="analysisResult = null" class="button-outline" style="padding: 2px 8px; font-size: 10px;">Schließen</button>
          </div>
          <p style="white-space: pre-wrap; margin-top: 10px; font-size: 13px;">{{ analysisResult }}</p>
        </div>
      }
    
      <label style="display: block; margin-bottom: 10px;">
        Filter
        <input [(ngModel)]="filterText" placeholder="z.B. Benutzer, Aktion, Detail" />
      </label>

      <div class="row" style="margin-bottom: 10px; gap: 8px;">
        <button class="button-outline" [class.active-toggle]="viewMode === 'timeline'" (click)="viewMode = 'timeline'">Timeline</button>
        <button class="button-outline" [class.active-toggle]="viewMode === 'table'" (click)="viewMode = 'table'">Tabelle</button>
      </div>

      @if (viewMode === 'timeline') {
        <div class="timeline">
          @for (log of filteredLogs; track log) {
            <div class="event-row">
              <div class="event-dot" [class]="actionTone(log.action)"></div>
              <div class="event-card">
                <div class="event-head">
                  <span class="event-action">{{ log.action }}</span>
                  <span class="muted">{{ formatTime(log.timestamp) }}</span>
                </div>
                <div class="event-meta">
                  <span><strong>{{ log.username || 'system' }}</strong></span>
                  <span class="muted">{{ log.ip || '-' }}</span>
                </div>
                <div class="event-summary">{{ summaryFor(log) }}</div>
                @if (log.details) {
                  <details>
                    <summary>Details</summary>
                    <div style="font-size: 11px; margin-top: 6px;">
                      @for (entry of getDetailsEntries(log.details); track entry) {
                        <div><span class="muted">{{ entry.key }}:</span> {{ entry.value }}</div>
                      }
                    </div>
                  </details>
                }
              </div>
            </div>
          }
          @if (filteredLogs.length === 0) {
            <div class="muted">Keine Audit-Logs gefunden.</div>
          }
        </div>
      }

      @if (viewMode === 'table') {
        <div style="overflow-x: auto;">
        <table>
          <thead>
            <tr>
              <th>Zeitstempel</th>
              <th>Benutzer</th>
              <th>IP</th>
              <th>Aktion</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            @for (log of filteredLogs; track log) {
              <tr>
                <td style="white-space: nowrap;">{{ formatTime(log.timestamp) }}</td>
                <td><strong>{{ log.username }}</strong></td>
                <td><small>{{ log.ip }}</small></td>
                <td><span class="badge">{{ log.action }}</span></td>
                <td>
                  @if (log.details) {
                    <div style="font-size: 10px; line-height: 1.2;">
                      @for (entry of getDetailsEntries(log.details); track entry) {
                        <div>
                          <span class="muted">{{ entry.key }}:</span> <span>{{ entry.value }}</span>
                        </div>
                      }
                    </div>
                  }
                </td>
              </tr>
            }
            @if (filteredLogs.length === 0) {
              <tr>
                <td colspan="5" style="text-align: center;" class="muted">Keine Audit-Logs gefunden.</td>
              </tr>
            }
          </tbody>
        </table>
      </div>
      }
    
      @if (logs.length > 0) {
        <div class="row" style="margin-top: 15px; justify-content: center;">
          <button (click)="prevPage()" [disabled]="offset === 0" class="button-outline">Zurück</button>
          <span style="margin: 0 15px; align-self: center;">Offset: {{offset}}</span>
          <button (click)="nextPage()" [disabled]="logs.length < limit" class="button-outline">Weiter</button>
        </div>
      }
    </div>
    `,
  styles: [`
    .badge {
      background: #e9ecef;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: bold;
      text-transform: uppercase;
    }
    .active-toggle {
      background: var(--accent);
      color: white;
      border-color: var(--accent);
    }
    .timeline {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .event-row {
      display: grid;
      grid-template-columns: 14px 1fr;
      gap: 10px;
      align-items: start;
    }
    .event-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      margin-top: 7px;
      background: #6c757d;
      box-shadow: 0 0 0 3px rgba(108, 117, 125, 0.15);
    }
    .event-dot.success { background: #198754; box-shadow: 0 0 0 3px rgba(25, 135, 84, 0.15); }
    .event-dot.warn { background: #ffc107; box-shadow: 0 0 0 3px rgba(255, 193, 7, 0.2); }
    .event-dot.danger { background: #dc3545; box-shadow: 0 0 0 3px rgba(220, 53, 69, 0.15); }
    .event-dot.info { background: #0d6efd; box-shadow: 0 0 0 3px rgba(13, 110, 253, 0.15); }
    .event-card {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px 10px;
      background: var(--bg);
    }
    .event-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      font-size: 12px;
    }
    .event-action {
      font-weight: 600;
      text-transform: uppercase;
    }
    .event-meta {
      margin-top: 4px;
      display: flex;
      gap: 10px;
      font-size: 12px;
    }
    .event-summary {
      margin-top: 6px;
      font-size: 12px;
    }
  `]
})
export class AuditLogComponent implements OnInit {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);
  private zone = inject(NgZone);
  private cdr = inject(ChangeDetectorRef);

  logs: any[] = [];
  limit = 20;
  offset = 0;
  filterText = "";
  analyzing = false;
  analysisResult: string | null = null;
  viewMode: 'timeline' | 'table' = 'timeline';

  ngOnInit() {
    this.loadLogs();
  }

  private getHubAgent() {
    return this.dir.list().find(a => a.role === 'hub');
  }

  loadLogs() {
    const hub = this.getHubAgent();
    if (!hub) {
        this.ns.error('Kein Hub-Agent gefunden');
        return;
    }
    this.hubApi.getAuditLogs(hub.url, this.limit, this.offset).subscribe({
      next: (data) => {
        this.zone.run(() => {
          if (Array.isArray(data)) {
            this.logs = data;
            this.cdr.detectChanges();
            return;
          }
          const nested = (data as any)?.data;
          this.logs = Array.isArray(nested) ? nested : [];
          this.cdr.detectChanges();
        });
      },
      error: (err) => this.zone.run(() => {
        this.ns.error('Audit-Logs konnten nicht geladen werden');
        this.cdr.detectChanges();
      })
    });
  }

  analyzeLogs() {
    const hub = this.getHubAgent();
    if (!hub) return;
    this.analyzing = true;
    this.analysisResult = null;
    this.hubApi.analyzeAuditLogs(hub.url).subscribe({
      next: (res) => {
        this.zone.run(() => {
          this.analysisResult = res.analysis;
          this.analyzing = false;
          this.cdr.detectChanges();
        });
      },
      error: (err) => {
        this.zone.run(() => {
          this.ns.error('Fehler bei der KI-Analyse');
          this.analyzing = false;
          this.cdr.detectChanges();
        });
      }
    });
  }

  formatTime(ts: number): string {
    return new Date(ts * 1000).toLocaleString();
  }

  nextPage() {
    this.offset += this.limit;
    this.loadLogs();
  }

  prevPage() {
    this.offset = Math.max(0, this.offset - this.limit);
    this.loadLogs();
  }

  getDetailsEntries(details: any) {
    if (!details) return [];
    if (typeof details === 'string') {
        try {
            details = JSON.parse(details);
        } catch(e) {
            return [{key: 'info', value: details}];
        }
    }
    return Object.entries(details).map(([key, value]) => ({ 
        key, 
        value: typeof value === 'object' ? JSON.stringify(value) : value 
    }));
  }

  summaryFor(log: any): string {
    const details = this.parseDetails(log?.details);
    const keys = [
      details?.team_id ? `team=${details.team_id}` : '',
      details?.team_type_id ? `team_type=${details.team_type_id}` : '',
      details?.template_id ? `template=${details.template_id}` : '',
      details?.role_id ? `role=${details.role_id}` : '',
      details?.name ? `name=${details.name}` : '',
      details?.new_user ? `user=${details.new_user}` : '',
      details?.target_user ? `target=${details.target_user}` : '',
    ].filter(Boolean);
    if (keys.length) return keys.join(' | ');
    return 'Keine zusaetzlichen Details';
  }

  actionTone(action: string): 'danger' | 'success' | 'warn' | 'info' {
    const a = String(action || '').toLowerCase();
    if (a.includes('delete') || a.includes('blocked') || a.includes('failed')) return 'danger';
    if (a.includes('created') || a.includes('updated') || a.includes('enabled') || a.includes('setup')) return 'success';
    if (a.includes('lockout') || a.includes('banned')) return 'warn';
    return 'info';
  }

  private parseDetails(details: any): any {
    if (!details) return {};
    if (typeof details === 'string') {
      try {
        return JSON.parse(details);
      } catch {
        return { info: details };
      }
    }
    return details;
  }

  get filteredLogs() {
    const query = this.filterText.trim().toLowerCase();
    if (!query) return this.logs;
    return this.logs.filter(log => {
      const details = log.details ? JSON.stringify(log.details) : "";
      return [
        log.username,
        log.action,
        log.ip,
        details
      ].some(value => String(value || "").toLowerCase().includes(query));
    });
  }
}
