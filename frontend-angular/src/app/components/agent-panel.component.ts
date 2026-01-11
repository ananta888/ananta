import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { AgentDirectoryService, AgentEntry } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';

@Component({
  standalone: true,
  selector: 'app-agent-panel',
  imports: [CommonModule, FormsModule],
  template: `
    <h2>Agent Panel – {{agent?.name}}</h2>
    <p class="muted">{{agent?.url}}</p>

    <div class="card grid">
      <label>Prompt
        <textarea [(ngModel)]="prompt" rows="6" placeholder="REASON/COMMAND Format"></textarea>
      </label>
      <label>Command (manuell)
        <input [(ngModel)]="command" placeholder="z. B. echo hello" />
      </label>
      <div class="row">
        <button (click)="onPropose()" [disabled]="busy">Vorschlag holen</button>
        <button (click)="onExecute()" [disabled]="busy || !command">Ausführen</button>
        <span class="muted" *ngIf="busy">Bitte warten…</span>
      </div>
      <div *ngIf="reason || command" class="grid">
        <div><strong>Reason:</strong> {{reason}}</div>
        <div><strong>Command:</strong> <code>{{command}}</code></div>
      </div>
      <div *ngIf="execOut" class="card">
        <div><strong>Exit:</strong> {{execExit}}</div>
        <pre style="white-space: pre-wrap">{{execOut}}</pre>
      </div>
    </div>

    <div class="card" *ngIf="logs?.length">
      <h3>Letzte Logs</h3>
      <div class="grid">
        <div *ngFor="let l of logs" class="row" style="justify-content: space-between;">
          <code style="max-width:70%">{{l.command}}</code>
          <span class="muted">{{l.returncode}}</span>
        </div>
      </div>
    </div>
  `
})
export class AgentPanelComponent {
  agent?: AgentEntry;
  prompt = '';
  reason = '';
  command = '';
  execOut = '';
  execExit: any = '';
  busy = false;
  logs: any[] = [];

  constructor(private route: ActivatedRoute, private dir: AgentDirectoryService, private api: AgentApiService) {
    const name = this.route.snapshot.paramMap.get('name')!;
    this.agent = this.dir.get(name);
    if (!this.agent) return;
    this.loadLogs();
  }

  onPropose() {
    if (!this.agent) return;
    this.busy = true;
    this.api.propose(this.agent.url, { prompt: this.prompt }).subscribe({
      next: (r: any) => { this.reason = r?.reason || ''; this.command = r?.command || ''; },
      error: () => {},
      complete: () => { this.busy = false; }
    });
  }
  onExecute() {
    if (!this.agent || !this.command) return;
    this.busy = true;
    this.api.execute(this.agent.url, { command: this.command }, this.agent.token).subscribe({
      next: (r: any) => { this.execOut = r?.stdout || ''; this.execExit = r?.exit_code; this.loadLogs(); },
      error: () => { this.execOut = 'Fehler bei Ausführung'; },
      complete: () => { this.busy = false; }
    });
  }
  loadLogs() {
    if (!this.agent) return;
    this.api.logs(this.agent.url, 50).subscribe({ next: (r: any) => this.logs = r || [] });
  }
}
