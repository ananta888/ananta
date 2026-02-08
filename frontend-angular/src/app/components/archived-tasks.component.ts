import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-archived-tasks',
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>Archivierte Tasks</h2>
      <div class="row" style="gap: 10px;">
        <input [(ngModel)]="searchText" placeholder="Suchen..." style="width: 200px;">
        <button (click)="reload()" class="button-outline">🔄</button>
      </div>
    </div>
    
    <p class="muted" *ngIf="!hub">Kein Hub-Agent konfiguriert.</p>

    <div *ngIf="hub">
      <div class="card">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Titel</th>
              <th>Status (alt)</th>
              <th>Archiviert am</th>
              <th>Aktionen</th>
            </tr>
          </thead>
          <tbody>
            <tr *ngFor="let t of filteredTasks()">
              <td><small class="muted">{{t.id.substring(0,8)}}</small></td>
              <td>{{t.title}}</td>
              <td><span class="tag">{{t.status}}</span></td>
              <td>{{(t.archived_at * 1000) | date:'short'}}</td>
              <td>
                <button class="button-small" (click)="restore(t.id)">Wiederherstellen</button>
              </td>
            </tr>
            <tr *ngIf="filteredTasks().length === 0">
              <td colspan="5" style="text-align: center;" class="muted">Keine archivierten Tasks gefunden.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <style>
      .tag { font-size: 10px; padding: 2px 6px; border-radius: 10px; border: 1px solid #ccc; background: #f0f0f0; }
      table { width: 100%; border-collapse: collapse; }
      th, td { text-align: left; padding: 12px; border-bottom: 1px solid #eee; }
      .button-small { padding: 4px 8px; font-size: 12px; }
    </style>
  `
})
export class ArchivedTasksComponent {
  hub = this.dir.list().find(a => a.role === 'hub');
  tasks: any[] = [];
  searchText = '';

  constructor(
    private dir: AgentDirectoryService,
    private hubApi: HubApiService,
    private ns: NotificationService
  ) {
    this.reload();
  }

  reload() {
    if (!this.hub) return;
    this.hubApi.listArchivedTasks(this.hub.url).subscribe({
      next: r => this.tasks = Array.isArray(r) ? r : [],
      error: err => this.ns.error('Fehler beim Laden der archivierten Tasks')
    });
  }

  filteredTasks() {
    if (!this.searchText) return this.tasks;
    const s = this.searchText.toLowerCase();
    return this.tasks.filter(t => 
      (t.title || '').toLowerCase().includes(s) || 
      (t.description || '').toLowerCase().includes(s) ||
      (t.id || '').toLowerCase().includes(s)
    );
  }

  restore(id: string) {
    if (!this.hub) return;
    this.hubApi.restoreTask(this.hub.url, id).subscribe({
      next: () => {
        this.ns.success('Task wiederhergestellt');
        this.reload();
      },
      error: err => this.ns.error('Fehler beim Wiederherstellen')
    });
  }
}
