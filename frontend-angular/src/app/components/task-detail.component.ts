import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';

@Component({
  standalone: true,
  selector: 'app-task-detail',
  imports: [CommonModule, FormsModule],
  template: `
    <h2>Task {{task?.id}}</h2>
    <p class="muted">{{task?.title}}</p>

    <div class="card grid" *ngIf="task">
      <div class="row">
        <label>Status
          <select [(ngModel)]="task.status">
            <option value="backlog">backlog</option>
            <option value="to-do">to-do</option>
            <option value="in-progress">in-progress</option>
            <option value="done">done</option>
          </select>
        </label>
        <button (click)="saveStatus()">Speichern</button>
      </div>

      <div class="row">
        <label>Agent
          <select [(ngModel)]="assignUrl">
            <option [ngValue]="undefined">– none –</option>
            <option *ngFor="let a of allAgents" [ngValue]="a.url">{{a.name}} ({{a.role||'worker'}})</option>
          </select>
        </label>
        <button (click)="saveAssign()">Zuweisen</button>
      </div>

      <div class="grid">
        <label>Prompt
          <textarea [(ngModel)]="prompt" rows="5" placeholder="Optional – sonst Beschreibung/Main Prompt"></textarea>
        </label>
        <div class="row">
          <button (click)="propose()" [disabled]="busy">Vorschlag holen</button>
          <button (click)="execute()" [disabled]="busy || !proposed">Ausführen</button>
          <span class="muted" *ngIf="busy">Bitte warten…</span>
        </div>
        <div *ngIf="proposed" class="card">
          <div><strong>Command:</strong> <code>{{proposed}}</code></div>
        </div>
      </div>
    </div>

    <div class="card" *ngIf="logs?.length">
      <h3>Logs</h3>
      <div *ngFor="let l of logs">
        <div class="row" style="justify-content: space-between;">
          <code style="max-width:70%">{{l.command}}</code>
          <span class="muted">{{l.returncode}}</span>
        </div>
      </div>
    </div>
  `
})
export class TaskDetailComponent {
  hub = this.dir.list().find(a => a.role === 'hub');
  task: any;
  logs: any[] = [];
  allAgents = this.dir.list();
  assignUrl: string | undefined;
  prompt = '';
  proposed = '';
  busy = false;

  constructor(private route: ActivatedRoute, private dir: AgentDirectoryService, private hubApi: HubApiService) {
    this.reload();
  }
  get tid(){ return this.route.snapshot.paramMap.get('id')!; }
  reload(){ if(!this.hub) return; this.hubApi.getTask(this.hub.url, this.tid).subscribe({ next: t => { this.task = t; this.assignUrl = t?.assignment?.agent_url; this.loadLogs(); this.proposed = t?.last_proposed_command || ''; } }); }
  loadLogs(){ if(!this.hub) return; this.hubApi.taskLogs(this.hub.url, this.tid).subscribe({ next: r => this.logs = r||[] }); }
  saveStatus(){ if(!this.hub || !this.task) return; this.hubApi.patchTask(this.hub.url, this.tid, { status: this.task.status }, this.hub.token).subscribe({ next: () => this.reload() }); }
  saveAssign(){
    if(!this.hub) return;
    const sel = this.allAgents.find(a => a.url === this.assignUrl);
    this.hubApi.assign(this.hub.url, this.tid, { agent_url: this.assignUrl, token: sel?.token }, this.hub.token).subscribe({ next: () => this.reload() });
  }
  propose(){
    if(!this.hub) return; this.busy = true;
    this.hubApi.propose(this.hub.url, this.tid, { prompt: this.prompt }).subscribe({ next: (r:any) => { this.proposed = r?.command || ''; }, complete: () => this.busy=false });
  }
  execute(){
    if(!this.hub || !this.proposed) return; this.busy = true;
    this.hubApi.execute(this.hub.url, this.tid, { command: this.proposed }, this.hub.token).subscribe({ next: () => { this.loadLogs(); }, complete: () => this.busy=false });
  }
}
