import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-archived-tasks',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>Archivierte Tasks</h2>
      <div class="row" style="gap: 10px; flex-wrap: wrap;">
        <input [(ngModel)]="searchText" placeholder="Titel/ID suchen..." style="width: 200px;">
        <div class="row" style="gap: 5px; align-items: center;">
          <label style="font-size: 12px;">Von:</label>
          <input type="date" [(ngModel)]="fromDate" style="width: 130px; padding: 4px;">
        </div>
        <div class="row" style="gap: 5px; align-items: center;">
          <label style="font-size: 12px;">Bis:</label>
          <input type="date" [(ngModel)]="toDate" style="width: 130px; padding: 4px;">
        </div>
        <button (click)="reload()" class="button-outline">🔄</button>
      </div>
    </div>
    
    @if (!hub) {
      <p class="muted">Kein Hub-Agent konfiguriert.</p>
    }
    
    @if (hub) {
      <div>
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
              @for (t of filteredTasks(); track t) {
                <tr>
                  <td><small class="muted">{{t.id.substring(0,8)}}</small></td>
                  <td>{{t.title}}</td>
                  <td><span class="tag">{{t.status}}</span></td>
                  <td>{{(t.archived_at * 1000) | date:'short'}}</td>
                  <td>
                    <button class="button-small" (click)="restore(t.id)">Wiederherstellen</button>
                  </td>
                </tr>
              }
              @if (filteredTasks().length === 0) {
                <tr>
                  <td colspan="5" style="text-align: center;" class="muted">Keine archivierten Tasks gefunden.</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </div>
    }
    
    <style>
      .tag { font-size: 10px; padding: 2px 6px; border-radius: 10px; border: 1px solid #ccc; background: #f0f0f0; }
      table { width: 100%; border-collapse: collapse; }
      th, td { text-align: left; padding: 12px; border-bottom: 1px solid #eee; }
      .button-small { padding: 4px 8px; font-size: 12px; }
    </style>
    `
})
export class ArchivedTasksComponent {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);

  hub = this.dir.list().find(a => a.role === 'hub');
  tasks: any[] = [];
  searchText = '';
  fromDate = '';
  toDate = '';

  constructor() {
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
    let filtered = this.tasks;

    if (this.searchText) {
      const s = this.searchText.toLowerCase();
      filtered = filtered.filter(t => 
        (t.title || '').toLowerCase().includes(s) || 
        (t.description || '').toLowerCase().includes(s) ||
        (t.id || '').toLowerCase().includes(s)
      );
    }

    if (this.fromDate) {
      const from = new Date(this.fromDate).getTime() / 1000;
      filtered = filtered.filter(t => t.archived_at >= from);
    }

    if (this.toDate) {
      // + 86399 um den ganzen Endtag einzuschließen
      const to = new Date(this.toDate).getTime() / 1000 + 86399;
      filtered = filtered.filter(t => t.archived_at <= to);
    }

    return filtered;
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
