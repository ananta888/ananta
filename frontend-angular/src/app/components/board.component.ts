import { Component, DoCheck, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { DragDropModule, CdkDragDrop } from '@angular/cdk/drag-drop';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
import { normalizeTaskStatus } from '../utils/task-status';
import { TaskStatusDisplayPipe } from '../pipes/task-status-display.pipe';

@Component({
  standalone: true,
  selector: 'app-board',
  imports: [FormsModule, RouterLink, DragDropModule, TaskStatusDisplayPipe],
  template: `
    <div class="row flex-between">
      <h2>Board</h2>
      <div class="row board-toolbar">
        <input class="board-search" [(ngModel)]="searchText" placeholder="Suchen..." aria-label="Tasks durchsuchen">
        <div class="button-group">
          <button (click)="view = 'board'" [class.secondary]="view !== 'board'" aria-label="Sprint Board Ansicht" [attr.aria-pressed]="view === 'board'">Sprint Board</button>
          <button (click)="view = 'scrum'" [class.secondary]="view !== 'scrum'" aria-label="Scrum Insights Ansicht" [attr.aria-pressed]="view === 'scrum'">Scrum Insights</button>
        </div>
        <button (click)="reload()" class="button-outline" aria-label="Board aktualisieren">Refresh</button>
      </div>
    </div>
    @if (!hub) {
      <p class="muted">Kein Hub-Agent konfiguriert.</p>
    }

    @if (hub && view === 'board') {
      <div>
        <div class="card row gap-sm flex-end">
          <label for="new-task-input">Neuer Task
            <input id="new-task-input" [(ngModel)]="newTitle" placeholder="Task title" aria-required="true" />
          </label>
          <button (click)="create()" [disabled]="!newTitle" data-test="btn-create-task" aria-label="Neuen Task anlegen">Anlegen</button>
          @if (err) {
            <span class="danger" role="alert">{{err}}</span>
          }
        </div>
        @if (tasks.length === 0) {
          <div class="card empty-state mb-lg">
            <h3 class="no-margin">Noch keine Tasks vorhanden</h3>
            <p class="muted no-margin">
              Erstellen Sie Ihren ersten Task mit dem Formular oben.
            </p>
          </div>
        }
        @if (loading) {
          <div class="grid cols-2 board-grid">
            @for (col of boardColumns; track col) {
              <div class="card board-column">
                <h3>{{col.label}}</h3>
                <div class="board-dropzone">
                  <div class="skeleton line skeleton-line-40"></div>
                  <div class="skeleton line skeleton-line-40"></div>
                  <div class="skeleton line skeleton-line-40"></div>
                </div>
              </div>
            }
          </div>
        }
        @if (!loading) {
          <div class="grid cols-2 board-grid">
            @for (col of boardColumns; track col) {
              <div class="card board-column"
                cdkDropList
                [id]="col.id"
                [cdkDropListData]="tasksBy(col.id)"
                [cdkDropListConnectedTo]="dropListIds"
                (cdkDropListDropped)="onDrop($event, col.id)"
                role="region"
                [attr.aria-label]="col.label + ' Spalte'">
                <h3>{{col.label}}</h3>
                <div class="board-dropzone">
                  @for (t of tasksBy(col.id); track t) {
                    <div
                      class="board-item row"
                      cdkDrag
                      [cdkDragData]="t"
                      role="button"
                      tabindex="0"
                      [attr.aria-label]="'Task: ' + t.title">
                      <a [routerLink]="['/task', t.id]" [attr.aria-label]="'Task Details für ' + t.title">{{t.title}}</a>
                      @if (t.priority) {
                        <span class="muted font-sm">{{t.priority}}</span>
                      }
                    </div>
                  }
                  @if (!tasksBy(col.id).length) {
                    <div class="muted board-empty">Keine Tasks</div>
                  }
                </div>
              </div>
            }
          </div>
        }
      </div>
    }

    @if (hub && view === 'scrum') {
      <div>
        <div class="grid cols-2">
          <div class="card">
            <h3>Burndown Chart</h3>
            <div class="burndown-chart">
              <svg width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Burndown Chart zeigt Fortschritt von Tasks über Zeit">
                <line x1="0" y1="0" x2="100" y2="100" stroke="gray" stroke-dasharray="2" />
                <polyline [attr.points]="'0,0 20,15 40,40 60,45 80,70 100,' + getBurndownValue()" fill="none" stroke="red" stroke-width="2" />
              </svg>
              <div class="burndown-chart-legend">
                <span>Start</span><span>Mitte</span><span>Ende</span>
              </div>
            </div>
            <p class="muted font-sm text-center">Done: {{tasksBy('completed').length}} / Total: {{tasks.length}}</p>
          </div>
          <div class="card">
            <h3>Roadmap</h3>
            <div class="muted font-sm mb-sm">Blocked: {{ tasksBy('blocked').length }} | In Progress: {{ tasksBy('in_progress').length }}</div>
            @for (t of getRoadmapTasks(); track t) {
              <div class="roadmap-task">
                <strong>{{t.title}}</strong>
                <div class="muted font-sm">{{t.description?.substring(0, 100)}}...</div>
                <div class="mt-sm">
                  <span class="tag" [class.tag-success]="normalizeTaskStatus(t.status) === 'completed'" [class.tag-warning]="normalizeTaskStatus(t.status) !== 'completed'">{{t.status | taskStatusDisplay}}</span>
                </div>
              </div>
            }
            @if (getRoadmapTasks().length === 0) {
              <div class="muted">Keine Roadmap-Tasks gefunden.</div>
            }
          </div>
        </div>
      </div>
    }
  `
})
export class BoardComponent implements DoCheck {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);

  hub = this.dir.list().find(a => a.role === 'hub');
  tasks: any[] = [];
  newTitle = '';
  searchText = localStorage.getItem('ananta.board.search') || '';
  err = '';
  loading = false;
  view: 'board' | 'scrum' = (localStorage.getItem('ananta.board.view') as 'board' | 'scrum') || 'board';
  boardColumns = [
    { id: 'todo', label: 'To-Do' },
    { id: 'in_progress', label: 'In-Progress' },
    { id: 'blocked', label: 'Blocked' },
    { id: 'completed', label: 'Done' }
  ];
  dropListIds = this.boardColumns.map(col => col.id);

  constructor() {
    this.reload();
  }
  reload(){
    if(!this.hub) return;
    this.loading = true;
    this.hubApi.listTasks(this.hub.url).subscribe({
      next: r => {
        this.tasks = Array.isArray(r) ? r : [];
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      }
    });
  }
  normalizeTaskStatus = normalizeTaskStatus;

  ngDoCheck() {
    localStorage.setItem('ananta.board.search', this.searchText || '');
    localStorage.setItem('ananta.board.view', this.view);
  }

  tasksBy(status: string) {
    if (!Array.isArray(this.tasks)) return [];
    return this.tasks.filter((t: any) => {
      const normalized = this.normalizeTaskStatus(t.status);
      const desired = this.normalizeTaskStatus(status);
      const matchStatus = normalized === desired;
      const matchSearch = !this.searchText ||
        (t.title || '').toLowerCase().includes(this.searchText.toLowerCase()) ||
        (t.description || '').toLowerCase().includes(this.searchText.toLowerCase());
      return matchStatus && matchSearch;
    });
  }

  getBurndownValue() {
    const total = this.tasks.length || 1;
    const done = this.tasksBy('completed').length;
    return 100 - (done / total * 100);
  }

  getRoadmapTasks() {
    return this.tasks.filter(t => (t.title||'').toLowerCase().includes('roadmap') || this.normalizeTaskStatus(t.status) === 'todo');
  }

  onDrop(event: CdkDragDrop<any[]>, newStatus: string) {
    const task = event.item?.data;
    if (!this.hub || !task) return;
    const current = this.normalizeTaskStatus(task.status);
    const desired = this.normalizeTaskStatus(newStatus);
    if (current === desired) return;
    const previousStatus = task.status;
    task.status = desired;
    this.hubApi.patchTask(this.hub.url, task.id, { status: desired }).subscribe({
      next: () => this.ns.success(`Status auf ${desired} aktualisiert`),
      error: () => {
        task.status = previousStatus;
        this.ns.error('Status-Update fehlgeschlagen');
      }
    });
  }

  create(){
    if(!this.hub || !this.newTitle) return;
    this.hubApi.createTask(this.hub.url, { title: this.newTitle, status: 'todo' }).subscribe({
      next: () => { this.newTitle = ''; this.err = ''; this.reload(); },
      error: () => { this.err = 'Fehler beim Anlegen'; }
    });
  }
}
