import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AgentDirectoryService, AgentEntry } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';

@Component({
  standalone: true,
  selector: 'app-agents-list',
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="row" style="justify-content: space-between; align-items: flex-end;">
      <div>
        <h2>Agents</h2>
        <p class="muted">Verwalten Sie Ihre Agent-Instanzen (Hub & Worker).</p>
      </div>
      <div class="row">
        <button (click)="add()">Neu</button>
      </div>
    </div>

    <div class="grid cols-2">
      <div class="card" *ngFor="let a of agents">
        <div class="row" style="justify-content: space-between;">
          <div>
            <strong>{{a.name}}</strong>
            <span class="muted">({{a.role || 'worker'}})</span>
          </div>
          <div>
            <a [routerLink]="['/panel', a.name]">Panel</a>
          </div>
        </div>
        <div class="muted">{{a.url}}</div>
        <div class="row" style="margin-top:8px">
          <button (click)="ping(a)">Health</button>
          <span [class.success]="a['_health']==='ok'" [class.danger]="a['_health'] && a['_health']!=='ok'">{{a['_health']||''}}</span>
        </div>

        <details style="margin-top:8px">
          <summary>Bearbeiten</summary>
          <div class="grid">
            <label class="row">Name <input [(ngModel)]="a.name"></label>
            <label class="row">URL <input [(ngModel)]="a.url"></label>
            <label class="row">Token <input [(ngModel)]="a.token" placeholder="optional"></label>
            <label class="row">Rolle 
              <select [(ngModel)]="a.role">
                <option value="hub">hub</option>
                <option value="worker">worker</option>
              </select>
            </label>
          </div>
          <div class="row" style="margin-top:8px">
            <button (click)="save(a)">Speichern</button>
            <button (click)="remove(a)" class="danger">Löschen</button>
          </div>
        </details>
      </div>
    </div>
  `
})
export class AgentsListComponent {
  agents: (AgentEntry & { _health?: string })[] = [];
  constructor(private dir: AgentDirectoryService, private api: AgentApiService, private router: Router) {
    this.refresh();
  }
  refresh() { this.agents = this.dir.list() as any; }
  add() {
    const idx = this.agents.length + 1;
    const entry: AgentEntry = { name: `agent-${idx}`, url: 'http://localhost:5003', role: 'worker' };
    this.dir.upsert(entry); this.refresh();
  }
  save(a: AgentEntry) { this.dir.upsert(a); this.refresh(); }
  remove(a: AgentEntry) { this.dir.remove(a.name); this.refresh(); }
  ping(a: any) {
    this.api.health(a.url).subscribe({ next: () => a._health = 'ok', error: () => a._health = 'error' });
  }
}
