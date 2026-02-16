import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { DragDropModule, CdkDragDrop } from '@angular/cdk/drag-drop';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-board',
  imports: [CommonModule, FormsModule, RouterLink, DragDropModule],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>Board</h2>
      <div class="row" style="gap: 10px;">
        <input [(ngModel)]="searchText" placeholder="Suchen..." style="width: 200px;">
        <div class="button-group">
          <button (click)="view = 'board'" [class.secondary]="view !== 'board'">Sprint Board</button>
          <button (click)="view = 'scrum'" [class.secondary]="view !== 'scrum'">Scrum Insights</button>
        </div>
        <button (click)="reload()" class="button-outline">🔄</button>
      </div>
    </div>
    <p class="muted" *ngIf="!hub">Kein Hub-Agent konfiguriert.</p>

    <div *ngIf="hub && view === 'board'">
      <div class="card row" style="gap:8px; align-items: flex-end;">
        <label>Neuer Task
          <input [(ngModel)]="newTitle" placeholder="Task title" />
        </label>
        <button (click)="create()" [disabled]="!newTitle" data-test="btn-create-task">Anlegen</button>
        <span class="danger" *ngIf="err">{{err}}</span>
      </div>
      <div class="grid cols-2 board-grid">
        <div class="card board-column"
             *ngFor="let col of boardColumns"
             cdkDropList
             [id]="col.id"
             [cdkDropListData]="tasksBy(col.id)"
             [cdkDropListConnectedTo]="dropListIds"
             (cdkDropListDropped)="onDrop($event, col.id)">
          <h3>{{col.label}}</h3>
          <div class="board-dropzone">
            <div *ngFor="let t of tasksBy(col.id)"
                 class="board-item row"
                 cdkDrag
                 [cdkDragData]="t">
              <a [routerLink]="['/task', t.id]">{{t.title}}</a>
              <span class="muted" *ngIf="t.priority" style="font-size: 10px;">{{t.priority}}</span>
            </div>
            <div *ngIf="!tasksBy(col.id).length" class="muted board-empty">Keine Tasks</div>
          </div>
        </div>
      </div>
    </div>

    <div *ngIf="hub && view === 'scrum'">
      <div class="grid cols-2">
        <div class="card">
          <h3>🔥 Burndown Chart (Mock)</h3>
          <div style="height: 200px; border-left: 2px solid #333; border-bottom: 2px solid #333; position: relative; margin: 20px;">
            <svg width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none">
              <line x1="0" y1="0" x2="100" y2="100" stroke="gray" stroke-dasharray="2" />
              <polyline [attr.points]="'0,0 20,15 40,40 60,45 80,70 100,' + getBurndownValue()" fill="none" stroke="red" stroke-width="2" />
            </svg>
            <div style="position: absolute; bottom: -20px; width: 100%; display: flex; justify-content: space-between; font-size: 10px;">
              <span>Start</span><span>Mitte</span><span>Ende</span>
            </div>
          </div>
          <p class="muted" style="font-size: 12px; text-align: center;">Done: {{tasksBy('done').length}} / Total: {{tasks.length}}</p>
        </div>
        <div class="card">
          <h3>🗺️ Roadmap</h3>
          <div *ngFor="let t of getRoadmapTasks()" style="margin-bottom: 10px; padding: 8px; background: #f9f9f9; border-radius: 4px;">
            <strong>{{t.title}}</strong>
            <div class="muted" style="font-size: 12px;">{{t.description?.substring(0, 100)}}...</div>
            <div style="margin-top: 4px;">
               <span class="tag" [style.background]="t.status === 'done' ? '#d4edda' : '#fff3cd'">{{t.status}}</span>
            </div>
          </div>
          <div *ngIf="getRoadmapTasks().length === 0" class="muted">Keine Roadmap-Tasks gefunden.</div>
        </div>
      </div>
    </div>

    <style>
      .button-group { display: flex; }
      .button-group button { border-radius: 0; }
      .button-group button:first-child { border-radius: 4px 0 0 4px; }
      .button-group button:last-child { border-radius: 0 4px 4px 0; }
      .tag { font-size: 10px; padding: 2px 6px; border-radius: 10px; border: 1px solid #ccc; background: white; }
      .board-grid { align-items: start; }
      .board-column { min-height: 220px; }
      .board-dropzone { min-height: 120px; }
      .board-item {
        justify-content: space-between;
        margin-bottom: 6px;
        padding: 6px;
        border: 1px dashed #e2e8f0;
        border-radius: 6px;
        background: #fff;
        cursor: move;
      }
      .board-item:last-child { margin-bottom: 0; }
      .board-empty { font-size: 12px; margin-top: 6px; }
      .cdk-drag-preview {
        box-shadow: 0 8px 16px rgba(0,0,0,0.15);
        border-radius: 6px;
      }
      .cdk-drag-placeholder { opacity: 0.3; }
      .cdk-drop-list.cdk-drop-list-dragging .board-item:not(.cdk-drag-placeholder) {
        transition: transform 0.15s ease;
      }
    </style>
  `
})
export class BoardComponent {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);

  hub = this.dir.list().find(a => a.role === 'hub');
  tasks: any[] = [];
  newTitle = '';
  searchText = '';
  err = '';
  view: 'board' | 'scrum' = 'board';
  boardColumns = [
    { id: 'backlog', label: 'Backlog' },
    { id: 'to-do', label: 'To-Do' },
    { id: 'in-progress', label: 'In-Progress' },
    { id: 'done', label: 'Done' }
  ];
  dropListIds = this.boardColumns.map(col => col.id);

  constructor() {
    this.reload();
  }
  reload(){ if(!this.hub) return; this.hubApi.listTasks(this.hub.url).subscribe({ next: r => this.tasks = Array.isArray(r) ? r : [] }); }
  tasksBy(status: string) {
    if (!Array.isArray(this.tasks)) return [];
    return this.tasks.filter((t: any) => {
      const normalized = (t.status || '').toLowerCase().replace('_', '-');
      const desired = status.replace('_', '-');
      const matchStatus = normalized === desired || (desired === 'to-do' && normalized === 'todo');
      const matchSearch = !this.searchText || 
        (t.title || '').toLowerCase().includes(this.searchText.toLowerCase()) ||
        (t.description || '').toLowerCase().includes(this.searchText.toLowerCase());
      return matchStatus && matchSearch;
    });
  }
  
  getBurndownValue() {
    const total = this.tasks.length || 1;
    const done = this.tasksBy('done').length;
    return 100 - (done / total * 100);
  }

  getRoadmapTasks() {
    return this.tasks.filter(t => (t.title||'').toLowerCase().includes('roadmap') || (t.status||'') === 'backlog');
  }

  onDrop(event: CdkDragDrop<any[]>, newStatus: string) {
    const task = event.item?.data;
    if (!this.hub || !task) return;
    const current = (task.status || '').toLowerCase().replace('_', '-');
    if (current === newStatus) return;
    const previousStatus = task.status;
    task.status = newStatus;
    this.hubApi.patchTask(this.hub.url, task.id, { status: newStatus }).subscribe({
      next: () => this.ns.success(`Status auf ${newStatus} aktualisiert`),
      error: () => {
        task.status = previousStatus;
        this.ns.error('Status-Update fehlgeschlagen');
      }
    });
  }

  create(){
    if(!this.hub || !this.newTitle) return;
    this.hubApi.createTask(this.hub.url, { title: this.newTitle, status: 'backlog' }).subscribe({
      next: () => { this.newTitle = ''; this.err = ''; this.reload(); },
      error: () => { this.err = 'Fehler beim Anlegen'; }
    });
  }
}
