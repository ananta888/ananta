import { Component, AfterViewInit, ElementRef, OnInit, ViewChild, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import mermaid from 'mermaid/dist/mermaid.js';

import { HubApiService } from '../services/hub-api.service';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { normalizeTaskStatus, taskStatusDisplayLabel } from '../utils/task-status';

@Component({
  standalone: true,
  selector: 'app-task-graph',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap;">
      <h2>Task Abhaengigkeits-Graph</h2>
      <div class="row" style="gap: 8px; flex-wrap: wrap;">
        <input [(ngModel)]="searchText" (ngModelChange)="renderGraph()" placeholder="ID/Titel filtern..." style="width: 220px;" />
        <label style="display: flex; align-items: center; gap: 6px; font-size: 12px;">
          <input type="checkbox" [(ngModel)]="showCompleted" (ngModelChange)="renderGraph()" />
          Completed anzeigen
        </label>
        <label style="display: flex; align-items: center; gap: 6px; font-size: 12px;">
          <input type="checkbox" [(ngModel)]="showFailed" (ngModelChange)="renderGraph()" />
          Failed anzeigen
        </label>
        <button (click)="loadTasks()" class="button-outline">Aktualisieren</button>
        <button (click)="archiveVisibleDoneAndFailed()" class="button-outline">Sichtbare Done/Failed archivieren</button>
        <button (click)="deleteVisibleTestRunTasks()" class="button-outline danger">Sichtbare Testlauf-Tasks loeschen</button>
      </div>
    </div>

    <div class="card" style="min-height: 500px; overflow: auto; display: flex; justify-content: center; align-items: center;">
      <div #mermaidDiv class="mermaid-container">
        @if (loading) {
          <p class="muted">Lade Tasks...</p>
        }
        @if (!loading && filteredTasks().length === 0) {
          <p class="muted">Keine Tasks zum Anzeigen gefunden.</p>
        }
      </div>
    </div>

    @if (filteredTasks().length) {
      <div class="card mt-md">
        <h3 style="margin-top: 0;">Sichtbare Tasks</h3>
        <table style="width: 100%; border-collapse: collapse;">
          <thead>
            <tr>
              <th style="text-align: left; padding: 8px;">ID</th>
              <th style="text-align: left; padding: 8px;">Titel</th>
              <th style="text-align: left; padding: 8px;">Status</th>
              <th style="text-align: left; padding: 8px;">Aktion</th>
            </tr>
          </thead>
          <tbody>
            @for (t of filteredTasks().slice(0, 50); track t.id) {
              <tr>
                <td style="padding: 8px;">{{ t.id }}</td>
                <td style="padding: 8px;">{{ t.title || '-' }}</td>
                <td style="padding: 8px;">{{ t.status }}</td>
                <td style="padding: 8px;">
                  <button class="button-small" (click)="archiveTask(t.id)">Archivieren</button>
                  <button class="button-small danger" style="margin-left: 6px;" (click)="deleteTask(t.id)">Loeschen</button>
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    }

    <style>
      .mermaid-container {
        width: 100%;
        text-align: center;
      }

      .mermaid-container svg {
        max-width: 100%;
        height: auto;
      }
    </style>
  `,
})
export class TaskGraphComponent implements OnInit, AfterViewInit {
  private hubApi = inject(HubApiService);
  private dir = inject(AgentDirectoryService);
  private ns = inject(NotificationService);

  @ViewChild('mermaidDiv') mermaidDiv!: ElementRef;

  tasks: any[] = [];
  loading = false;
  searchText = '';
  showCompleted = false;
  showFailed = false;

  hub = this.dir.list().find((a) => a.role === 'hub');

  constructor() {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'default',
      securityLevel: 'loose',
    });
  }

  ngOnInit() {
    this.loadTasks();
  }

  ngAfterViewInit() {
    if (this.tasks.length > 0) {
      this.renderGraph();
    }
  }

  loadTasks() {
    if (!this.hub) return;
    this.loading = true;
    this.hubApi.listTasks(this.hub.url).subscribe({
      next: (r) => {
        this.tasks = Array.isArray(r) ? r : [];
        this.loading = false;
        setTimeout(() => this.renderGraph(), 0);
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  filteredTasks() {
    const q = (this.searchText || '').trim().toLowerCase();
    return this.tasks.filter((t: any) => {
      const status = normalizeTaskStatus(t.status);
      if (!this.showCompleted && status === 'completed') return false;
      if (!this.showFailed && status === 'failed') return false;
      if (!q) return true;
      return (
        String(t.id || '').toLowerCase().includes(q) ||
        String(t.title || '').toLowerCase().includes(q) ||
        String(t.description || '').toLowerCase().includes(q)
      );
    });
  }

  async renderGraph() {
    if (!this.mermaidDiv) return;

    const visibleTasks = this.filteredTasks();
    if (visibleTasks.length === 0) {
      this.mermaidDiv.nativeElement.innerHTML = '<p class="muted">Keine Tasks zum Anzeigen gefunden.</p>';
      return;
    }

    let graphDefinition = 'graph TD\n';
    const visibleIds = new Set(visibleTasks.map((t) => t.id));

    for (const t of visibleTasks) {
      const status = normalizeTaskStatus(t.status);
      const statusLabel = taskStatusDisplayLabel(status);
      let color = '#fff';
      if (status === 'completed') color = '#d4edda';
      else if (status === 'in_progress') color = '#fff3cd';
      else if (status === 'todo') color = '#f8f9fa';

      const title = String(t.title || t.id || '').replace(/"/g, '\\"');
      graphDefinition += `  ${t.id}["${title} (${statusLabel})"]\n`;
      graphDefinition += `  style ${t.id} fill:${color},stroke:#333,stroke-width:1px\n`;
    }

    for (const t of visibleTasks) {
      if (t.parent_task_id && visibleIds.has(t.parent_task_id)) {
        graphDefinition += `  ${t.parent_task_id} --> ${t.id}\n`;
      }
    }

    try {
      const id = `mermaid-${Math.random().toString(36).substring(2, 11)}`;
      const { svg } = await mermaid.render(id, graphDefinition);
      this.mermaidDiv.nativeElement.innerHTML = svg;
    } catch (e) {
      console.error('Mermaid render error:', e);
      this.mermaidDiv.nativeElement.innerHTML = '<p class="danger">Fehler beim Rendern des Graphen</p>';
    }
  }

  private doneOrFailedTaskIdsFromVisible(): string[] {
    return this.filteredTasks()
      .filter((t: any) => {
        const status = normalizeTaskStatus(t.status);
        return status === 'completed' || status === 'failed';
      })
      .map((t: any) => t.id);
  }

  private testRunTaskIdsFromVisible(): string[] {
    const marker = /(e2e|test|integration|smoke|qa|playwright|pytest)/i;
    return this.filteredTasks()
      .filter((t: any) => marker.test(String(t.id || '')) || marker.test(String(t.title || '')) || marker.test(String(t.description || '')))
      .map((t: any) => t.id);
  }

  archiveVisibleDoneAndFailed() {
    if (!this.hub) return;
    const taskIds = this.doneOrFailedTaskIdsFromVisible();
    if (!taskIds.length) {
      this.ns.info('Keine sichtbaren Done/Failed-Tasks gefunden.');
      return;
    }
    this.hubApi.cleanupTasks(this.hub.url, { mode: 'archive', task_ids: taskIds }).subscribe({
      next: (res: any) => {
        const archivedCount = Number(res?.archived_count || 0);
        this.ns.success(`${archivedCount} Task(s) archiviert.`);
        this.loadTasks();
      },
      error: () => this.ns.error('Archivierung fehlgeschlagen.'),
    });
  }

  deleteVisibleTestRunTasks() {
    if (!this.hub) return;
    const taskIds = this.testRunTaskIdsFromVisible();
    if (!taskIds.length) {
      this.ns.info('Keine sichtbaren Testlauf-Tasks gefunden.');
      return;
    }
    this.hubApi.cleanupTasks(this.hub.url, { mode: 'delete', task_ids: taskIds }).subscribe({
      next: (res: any) => {
        const deletedCount = Number(res?.deleted_count || 0);
        this.ns.success(`${deletedCount} Testlauf-Task(s) geloescht.`);
        this.loadTasks();
      },
      error: () => this.ns.error('Loeschen fehlgeschlagen.'),
    });
  }

  archiveTask(taskId: string) {
    if (!this.hub) return;
    this.hubApi.archiveTask(this.hub.url, taskId).subscribe({
      next: () => {
        this.ns.success(`Task ${taskId} archiviert.`);
        this.loadTasks();
      },
      error: () => this.ns.error('Task konnte nicht archiviert werden.'),
    });
  }

  deleteTask(taskId: string) {
    if (!this.hub) return;
    this.hubApi.cleanupTasks(this.hub.url, { mode: 'delete', task_ids: [taskId] }).subscribe({
      next: () => {
        this.ns.success(`Task ${taskId} geloescht.`);
        this.loadTasks();
      },
      error: () => this.ns.error('Task konnte nicht geloescht werden.'),
    });
  }
}
