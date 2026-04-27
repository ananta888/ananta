import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { TaskManagementFacade } from '../features/tasks/task-management.facade';

@Component({
  standalone: true,
  selector: 'app-archived-tasks',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap;">
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
        <button (click)="reload()" class="button-outline">Aktualisieren</button>
        <button (click)="deleteFiltered()" class="button-outline danger">Gefilterte loeschen</button>
      </div>
    </div>

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
            @for (t of filteredTasks(); track t.id) {
              <tr>
                <td><small class="muted">{{ t.id }}</small></td>
                <td>{{ t.title }}</td>
                <td><span class="tag">{{ t.status }}</span></td>
                <td>{{ (t.archived_at * 1000) | date:'short' }}</td>
                <td>
                  <button class="button-small" (click)="restore(t.id)">Wiederherstellen</button>
                  <button class="button-small danger" style="margin-left: 6px;" (click)="deleteArchived(t.id)">Loeschen</button>
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

    <style>
      .tag {
        font-size: 10px;
        padding: 2px 6px;
        border-radius: 10px;
        border: 1px solid #ccc;
        background: #f0f0f0;
      }

      table {
        width: 100%;
        border-collapse: collapse;
      }

      th, td {
        text-align: left;
        padding: 12px;
        border-bottom: 1px solid #eee;
      }

      .button-small {
        padding: 4px 8px;
        font-size: 12px;
      }
    </style>
  `,
})
export class ArchivedTasksComponent {
  private dir = inject(AgentDirectoryService);
  private ns = inject(NotificationService);
  private taskFacade = inject(TaskManagementFacade);

  hub: any | undefined;
  tasks: any[] = [];
  searchText = '';
  fromDate = '';
  toDate = '';

  constructor() {
    this.refreshHub();
    this.reload();
  }

  private refreshHub() {
    this.hub = this.dir.list().find((a) => a.role === 'hub') || { name: 'hub', url: 'http://127.0.0.1:5000', role: 'hub' };
  }

  reload() {
    this.refreshHub();
    if (!this.hub) return;
    this.taskFacade.listArchivedTasks(this.hub.url, undefined, 1000, 0).subscribe({
      next: (r) => (this.tasks = Array.isArray(r) ? r : []),
      error: () => this.ns.error('Fehler beim Laden der archivierten Tasks'),
    });
  }

  filteredTasks() {
    let filtered = this.tasks;

    if (this.searchText) {
      const s = this.searchText.toLowerCase();
      filtered = filtered.filter((t) =>
        (t.title || '').toLowerCase().includes(s) ||
        (t.description || '').toLowerCase().includes(s) ||
        (t.id || '').toLowerCase().includes(s)
      );
    }

    if (this.fromDate) {
      const from = new Date(this.fromDate).getTime() / 1000;
      filtered = filtered.filter((t) => t.archived_at >= from);
    }

    if (this.toDate) {
      const to = new Date(this.toDate).getTime() / 1000 + 86399;
      filtered = filtered.filter((t) => t.archived_at <= to);
    }

    return filtered;
  }

  restore(id: string) {
    this.refreshHub();
    if (!this.hub) return;
    this.taskFacade.restoreTask(this.hub.url, id).subscribe({
      next: () => {
        this.ns.success('Task wiederhergestellt');
        this.reload();
      },
      error: () => this.ns.error('Fehler beim Wiederherstellen'),
    });
  }

  deleteArchived(id: string) {
    this.refreshHub();
    if (!this.hub) return;
    this.taskFacade.deleteArchivedTask(this.hub.url, id).subscribe({
      next: () => {
        this.ns.success('Archiv-Task geloescht');
        this.reload();
      },
      error: () => this.ns.error('Fehler beim Loeschen des Archiv-Tasks'),
    });
  }

  deleteFiltered() {
    this.refreshHub();
    if (!this.hub) return;
    const ids = this.filteredTasks().map((t) => t.id);
    if (!ids.length) {
      this.ns.info('Keine gefilterten Archiv-Tasks zum Loeschen.');
      return;
    }
    this.taskFacade.cleanupArchivedTasks(this.hub.url, { task_ids: ids }).subscribe({
      next: (res: any) => {
        const deletedCount = Number(res?.deleted_count || 0);
        this.ns.success(`${deletedCount} Archiv-Task(s) geloescht.`);
        this.reload();
      },
      error: () => this.ns.error('Batch-Loeschen im Archiv fehlgeschlagen.'),
    });
  }
}
