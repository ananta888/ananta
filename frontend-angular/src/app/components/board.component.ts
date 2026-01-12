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
      <div class="row">
        <button (click)="reload()">Neu laden</button>
      </div>
    </div>
    <p class="muted" *ngIf="!hub">Kein Hub-Agent konfiguriert.</p>
    <div class="card row" *ngIf="hub" style="gap:8px; align-items: flex-end;">
      <label>Neuer Task
        <input [(ngModel)]="newTitle" placeholder="Task title" />
      </label>
      <button (click)="create()" [disabled]="!newTitle" data-test="btn-create-task">Anlegen</button>
      <span class="danger" *ngIf="err">{{err}}</span>
    </div>
    <div class="grid cols-2" *ngIf="hub">
      <div class="card">
        <h3>Backlog</h3>
        <ng-container *ngFor="let t of tasksBy('backlog')">
          <div class="row" style="justify-content: space-between;">
            <a [routerLink]="['/task', t.id]">{{t.title}}</a>
            <span class="muted">{{t.assignment?.agent_url ? 'assigned' : 'unassigned'}}</span>
          </div>
        </ng-container>
      </div>
      <div class="card">
        <h3>To-Do</h3>
        <div *ngFor="let t of tasksBy('to-do')">
          <a [routerLink]="['/task', t.id]">{{t.title}}</a>
        </div>
      </div>
      <div class="card">
        <h3>In-Progress</h3>
        <div *ngFor="let t of tasksBy('in-progress')">
          <a [routerLink]="['/task', t.id]">{{t.title}}</a>
        </div>
      </div>
      <div class="card">
        <h3>Done</h3>
        <div *ngFor="let t of tasksBy('done')">
          <a [routerLink]="['/task', t.id]">{{t.title}}</a>
        </div>
      </div>
    </div>
  `
})
export class BoardComponent {
  hub = this.dir.list().find(a => a.role === 'hub');
  tasks: any[] = [];
  newTitle = '';
  err = '';

  constructor(private dir: AgentDirectoryService, private hubApi: HubApiService) {
    this.reload();
  }
  reload(){ if(!this.hub) return; this.hubApi.listTasks(this.hub.url).subscribe({ next: r => this.tasks = r||[] }); }
  tasksBy(status: string){ return (this.tasks||[]).filter((t:any) => (t.status||'').toLowerCase()===status); }
  create(){
    if(!this.hub || !this.newTitle) return;
    this.hubApi.createTask(this.hub.url, { title: this.newTitle }, this.hub.token).subscribe({
      next: () => { this.newTitle = ''; this.err = ''; this.reload(); },
      error: () => { this.err = 'Fehler beim Anlegen'; }
    });
  }
}
