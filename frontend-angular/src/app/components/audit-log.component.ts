import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-audit-log',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card">
      <div class="row" style="justify-content: space-between; align-items: center;">
        <h3>Audit-Logs (Admin)</h3>
        <button (click)="loadLogs()" class="button-outline">🔄 Aktualisieren</button>
      </div>
      <p class="muted">Überblick über administrative Aktionen und Systemereignisse.</p>

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
            <tr *ngFor="let log of logs">
              <td style="white-space: nowrap;">{{ formatTime(log.timestamp) }}</td>
              <td><strong>{{ log.username }}</strong></td>
              <td><small>{{ log.ip }}</small></td>
              <td><span class="badge">{{ log.action }}</span></td>
              <td>
                <div *ngIf="log.details" style="font-size: 10px; line-height: 1.2;">
                  <div *ngFor="let entry of getDetailsEntries(log.details)">
                    <span class="muted">{{ entry.key }}:</span> <span>{{ entry.value }}</span>
                  </div>
                </div>
              </td>
            </tr>
            <tr *ngIf="logs.length === 0">
              <td colspan="5" style="text-align: center;" class="muted">Keine Audit-Logs gefunden.</td>
            </tr>
          </tbody>
        </table>
      </div>
      
      <div class="row" style="margin-top: 15px; justify-content: center;" *ngIf="logs.length > 0">
          <button (click)="prevPage()" [disabled]="offset === 0" class="button-outline">Zurück</button>
          <span style="margin: 0 15px; align-self: center;">Offset: {{offset}}</span>
          <button (click)="nextPage()" [disabled]="logs.length < limit" class="button-outline">Weiter</button>
      </div>
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
  `]
})
export class AuditLogComponent implements OnInit {
  logs: any[] = [];
  limit = 20;
  offset = 0;
  hub = this.dir.list().find(a => a.role === 'hub');

  constructor(
    private dir: AgentDirectoryService,
    private hubApi: HubApiService,
    private ns: NotificationService
  ) {}

  ngOnInit() {
    this.loadLogs();
  }

  loadLogs() {
    if (!this.hub) {
        this.ns.error('Kein Hub-Agent gefunden');
        return;
    }
    this.hubApi.getAuditLogs(this.hub.url, this.limit, this.offset).subscribe({
      next: (data) => this.logs = data,
      error: (err) => this.ns.error('Audit-Logs konnten nicht geladen werden')
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
}
