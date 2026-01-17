import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';

@Component({
  standalone: true,
  selector: 'app-board',
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>Board</h2>
      <div class="row" style="gap: 10px;">
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
      <div class="grid cols-2">
        <div class="card">
          <h3>Backlog</h3>
          <ng-container *ngFor="let t of tasksBy('backlog')">
            <div class="row" style="justify-content: space-between; margin-bottom: 5px; padding: 5px; border-bottom: 1px solid #eee;">
              <a [routerLink]="['/task', t.id]">{{t.title}}</a>
              <span class="muted" style="font-size: 10px;">{{t.priority || 'Medium'}}</span>
            </div>
          </ng-container>
        </div>
        <div class="card">
          <h3>To-Do</h3>
          <div *ngFor="let t of tasksBy('todo')" style="margin-bottom: 5px;">
            <a [routerLink]="['/task', t.id]">{{t.title}}</a>
          </div>
          <div *ngFor="let t of tasksBy('to-do')" style="margin-bottom: 5px;">
            <a [routerLink]="['/task', t.id]">{{t.title}}</a>
          </div>
        </div>
        <div class="card">
          <h3>In-Progress</h3>
          <div *ngFor="let t of tasksBy('in-progress')" style="margin-bottom: 5px;">
            <a [routerLink]="['/task', t.id]">{{t.title}}</a>
          </div>
        </div>
        <div class="card">
          <h3>Done</h3>
          <div *ngFor="let t of tasksBy('done')" style="margin-bottom: 5px;">
            <a [routerLink]="['/task', t.id]">{{t.title}}</a>
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
              <polyline points="0,0 20,15 40,40 60,45 80,70 100,{{ getBurndownValue() }}" fill="none" stroke="red" stroke-width="2" />
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
    </style>
  `
})
export class BoardComponent {
  hub = this.dir.list().find(a => a.role === 'hub');
  tasks: any[] = [];
  newTitle = '';
  err = '';
  view: 'board' | 'scrum' = 'board';

  constructor(private dir: AgentDirectoryService, private hubApi: HubApiService) {
    this.reload();
  }
  reload(){ if(!this.hub) return; this.hubApi.listTasks(this.hub.url).subscribe({ next: r => this.tasks = r||[] }); }
  tasksBy(status: string){ return (this.tasks||[]).filter((t:any) => (t.status||'').toLowerCase().replace('_', '-')===status.replace('_', '-')); }
  
  getBurndownValue() {
    const total = this.tasks.length || 1;
    const done = this.tasksBy('done').length;
    return 100 - (done / total * 100);
  }

  getRoadmapTasks() {
    return this.tasks.filter(t => (t.title||'').toLowerCase().includes('roadmap') || (t.status||'') === 'backlog');
  }

  create(){
    if(!this.hub || !this.newTitle) return;
    this.hubApi.createTask(this.hub.url, { title: this.newTitle, status: 'backlog' }).subscribe({
      next: () => { this.newTitle = ''; this.err = ''; this.reload(); },
      error: () => { this.err = 'Fehler beim Anlegen'; }
    });
  }
}
