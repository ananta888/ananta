import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-task-detail',
  imports: [CommonModule, FormsModule],
  styles: [`
    .tab-btn {
      padding: 8px 16px;
      border: none;
      background: none;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      font-weight: 500;
    }
    .tab-btn.active {
      border-bottom: 2px solid var(--primary-color, #007bff);
      color: var(--primary-color, #007bff);
    }
    .tab-btn:hover:not(.active) {
      background: #f0f0f0;
    }
  `],
  template: `
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>Task #{{tid}}</h2>
      <span class="badge" [class.success]="task?.status==='done'" [class.warning]="task?.status==='in-progress'">{{task?.status}}</span>
    </div>
    <p class="muted" style="margin-top: -10px; margin-bottom: 20px;">{{task?.title}}</p>

    <div class="row" style="margin-bottom: 16px; border-bottom: 1px solid #ddd;">
      <button class="tab-btn" [class.active]="activeTab === 'details'" (click)="activeTab = 'details'">Details</button>
      <button class="tab-btn" [class.active]="activeTab === 'interact'" (click)="activeTab = 'interact'">Interaktion</button>
      <button class="tab-btn" [class.active]="activeTab === 'logs'" (click)="activeTab = 'logs'">Logs</button>
    </div>

    <div class="card grid" *ngIf="activeTab === 'details' && task">
      <div class="grid cols-2">
        <label>Status
          <select [(ngModel)]="task.status" (change)="saveStatus()">
            <option value="backlog">backlog</option>
            <option value="to-do">to-do</option>
            <option value="in-progress">in-progress</option>
            <option value="done">done</option>
          </select>
        </label>
        <label>Zugewiesener Agent
          <select [(ngModel)]="assignUrl" (change)="saveAssign()">
            <option [ngValue]="undefined">– Nicht zugewiesen –</option>
            <option *ngFor="let a of allAgents" [ngValue]="a.url">{{a.name}} ({{a.role||'worker'}})</option>
          </select>
        </label>
      </div>
      <div style="margin-top: 10px;">
        <strong>Beschreibung:</strong>
        <p>{{task.description || 'Keine Beschreibung vorhanden.'}}</p>
      </div>
    </div>

    <div class="card grid" *ngIf="activeTab === 'interact'">
      <div class="grid">
        <label>Spezifischer Prompt (optional)
          <textarea [(ngModel)]="prompt" rows="5" placeholder="Überschreibt den Standard-Prompt für diesen Schritt..."></textarea>
        </label>
        <label>Vorgeschlagener Befehl
          <input [(ngModel)]="proposed" placeholder="Noch kein Befehl vorgeschlagen" />
        </label>
        <div class="row">
          <button (click)="propose()" [disabled]="busy">Vorschlag holen</button>
          <button (click)="execute()" [disabled]="busy || !proposed" class="success">Ausführen</button>
          <span class="muted" *ngIf="busy">Arbeite...</span>
        </div>
      </div>
    </div>

    <div class="card" *ngIf="activeTab === 'logs'">
      <h3>Task Logs</h3>
      <div class="grid" *ngIf="logs?.length; else noLogs">
        <div *ngFor="let l of logs" style="border-bottom: 1px solid #eee; padding: 8px 0;">
          <div class="row" style="justify-content: space-between;">
            <code style="word-break: break-all;">{{l.command}}</code>
            <span class="badge" [class.success]="l.returncode===0" [class.danger]="l.returncode!==0">RC: {{l.returncode}}</span>
          </div>
          <pre *ngIf="l.stdout" style="font-size: 11px; margin-top: 5px; background: #f4f4f4; padding: 4px;">{{l.stdout}}</pre>
        </div>
      </div>
      <ng-template #noLogs><p class="muted">Bisher wurden keine Aktionen für diesen Task geloggt.</p></ng-template>
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
  activeTab = 'details';

  constructor(private route: ActivatedRoute, private dir: AgentDirectoryService, private hubApi: HubApiService, private ns: NotificationService) {
    this.reload();
  }
  get tid(){ return this.route.snapshot.paramMap.get('id')!; }
  reload(){ 
    if(!this.hub) return; 
    this.hubApi.getTask(this.hub.url, this.tid).subscribe({ 
      next: t => { 
        this.task = t; 
        this.assignUrl = t?.assignment?.agent_url; 
        this.proposed = t?.last_proposed_command || ''; 
        if (this.activeTab === 'logs') this.loadLogs();
      },
      error: () => this.ns.error('Task konnte nicht geladen werden')
    }); 
  }
  loadLogs(){ 
    if(!this.hub) return; 
    this.hubApi.taskLogs(this.hub.url, this.tid).subscribe({ 
      next: r => this.logs = r||[],
      error: () => this.ns.error('Logs konnten nicht geladen werden')
    }); 
  }
  saveStatus(){ 
    if(!this.hub || !this.task) return; 
    this.hubApi.patchTask(this.hub.url, this.tid, { status: this.task.status }, this.hub.token).subscribe({ 
      next: () => {
        this.ns.success(`Status auf ${this.task.status} aktualisiert`);
        this.reload();
      },
      error: () => this.ns.error('Status-Update fehlgeschlagen')
    }); 
  }
  saveAssign(){
    if(!this.hub) return;
    const sel = this.allAgents.find(a => a.url === this.assignUrl);
    this.hubApi.assign(this.hub.url, this.tid, { agent_url: this.assignUrl, token: sel?.token }, this.hub.token).subscribe({ 
      next: () => {
        this.ns.success(this.assignUrl ? 'Agent zugewiesen' : 'Zuweisung aufgehoben');
        this.reload();
      },
      error: () => this.ns.error('Zuweisung fehlgeschlagen')
    });
  }
  propose(){
    if(!this.hub) return; 
    this.busy = true;
    this.hubApi.propose(this.hub.url, this.tid, { prompt: this.prompt }).subscribe({ 
      next: (r:any) => { 
        this.proposed = r?.command || ''; 
        this.ns.success('Vorschlag erhalten');
      }, 
      error: () => this.ns.error('Fehler beim Abrufen des Vorschlags'),
      complete: () => this.busy=false 
    });
  }
  execute(){
    if(!this.hub || !this.proposed) return; 
    this.busy = true;
    this.hubApi.execute(this.hub.url, this.tid, { command: this.proposed }, this.hub.token).subscribe({ 
      next: (r: any) => { 
        this.ns.success('Befehl ausgeführt');
        this.loadLogs(); 
      }, 
      error: () => this.ns.error('Ausführung fehlgeschlagen'),
      complete: () => this.busy=false 
    });
  }
}
