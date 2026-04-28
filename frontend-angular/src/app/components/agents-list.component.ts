import { Component, OnInit, OnDestroy, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AgentEntry } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { UiSkeletonComponent } from './ui-skeleton.component';
import { SystemFacade } from '../features/system/system.facade';

@Component({
  standalone: true,
  selector: 'app-agents-list',
  imports: [FormsModule, RouterLink, UiSkeletonComponent],
  template: `
    <div class="row" style="justify-content: space-between; align-items: flex-end;">
      <div>
        <h2>Agenten</h2>
        <p class="muted">Verwalten Sie Ihre Agent-Instanzen (Hub & Worker).</p>
      </div>
      <div class="row">
        <label class="row" style="gap: 4px; font-size: 13px;">
          Aktualisierung (s):
          <input type="number" [(ngModel)]="refreshInterval" (change)="startPolling()" style="width: 45px; padding: 2px 4px;">
        </label>
        <button (click)="add()">Neu</button>
            @if (loading()) {
              <div class="spinner" aria-label="Loading"></div>
            }
      </div>
    </div>
    
    <div class="grid cols-2">
      @for (a of agents; track a) {
        <div class="card">
          <div class="row" style="justify-content: space-between;">
            <div class="row" style="gap: 8px; align-items: center;">
              <div class="status-dot"
                [class.online]="resolvedStatus(a)==='online'"
                [class.offline]="resolvedStatus(a)==='offline'"
              [title]="resolvedStatus(a) || 'unbekannt'"></div>
              <strong>{{a.name}}</strong>
              <span class="muted">({{a.role || 'worker'}})</span>
              <span class="muted">{{ agentScopeLabel(a) }}</span>
            </div>
            <div>
              <a [href]="a.url + '/apidocs'" target="_blank" style="margin-right: 12px; font-size: 12px;">Swagger</a>
              <a [routerLink]="['/panel', a.name]">Panel</a>
            </div>
          </div>
          <div class="muted">{{a.url}}</div>
          <div class="row" style="margin-top:8px">
            <button (click)="ping(a)">Health</button>
            <select [(ngModel)]="a['_terminalMode']" style="max-width: 130px;">
              <option value="interactive">interaktiv</option>
              <option value="read">nur lesen</option>
            </select>
            <button class="button-outline" (click)="openTerminal(a)">Terminal oeffnen</button>
            @if (!loading()) {
              <span [class.success]="a['_health']==='ok'" [class.danger]="a['_health'] && a['_health']!=='ok'">{{a['_health']||''}}</span>
              <span style="margin-left:8px" [class.success]="a['_db']==='DB OK'" [class.danger]="a['_db'] && a['_db']!=='DB OK'">{{a['_db']||''}}</span>
            } @else {
              <app-ui-skeleton [count]="2" [columns]="2" [lineCount]="1" [card]="false" lineClass="skeleton pill"></app-ui-skeleton>
            }
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
              <button (click)="testAuth(a)" class="button-outline">Token testen</button>
              <button (click)="remove(a)" class="danger">Loeschen</button>
            </div>
          </details>
        </div>
      }
    </div>
    `
})
export class AgentsListComponent implements OnInit, OnDestroy {
  private system = inject(SystemFacade);
  private ns = inject(NotificationService);
  private router = inject(Router);

  agents: (AgentEntry & { _health?: string, _status?: string, _db?: string, _terminalMode?: 'interactive' | 'read' })[] = [];
  refreshInterval = 30;
  hub?: AgentEntry;

  constructor() {
    this.refresh();
  }

  ngOnInit() {
    this.startPolling();
  }

  startPolling() {
    this.system.connectAgentStatuses(this.hub?.url, this.refreshInterval * 1000);
  }

  ngOnDestroy() {
    this.system.disconnectAgentStatuses(this.hub?.url);
  }

  refresh() { 
    this.hub = this.system.resolveHubAgent();
    this.agents = this.system.listConfiguredAgents()
      .map(a => ({ ...a, _terminalMode: 'interactive' }))
      .sort((a, b) => this.agentSortKey(a).localeCompare(this.agentSortKey(b))) as any;
    this.system.reloadAgentStatuses();
  }

  updateBackendStatuses() {
    this.system.reloadAgentStatuses();
  }

  loading(): boolean {
    return this.system.agentStatusesLoading();
  }

  resolvedStatus(agent: AgentEntry): string {
    const status = this.system.agentStatus(agent.name);
    if (status) return status;
    return agent.role === 'hub' ? 'online' : 'offline';
  }

  add() {
    const idx = this.agents.length + 1;
    const entry: AgentEntry = { name: `agent-${idx}`, url: 'http://localhost:5003', role: 'worker' };
    this.system.upsertConfiguredAgent(entry); this.refresh();
  }
  save(a: AgentEntry) { this.system.upsertConfiguredAgent(a); this.refresh(); }
  testAuth(a: AgentEntry) {
    this.system.getConfig(a.url).subscribe({
      next: () => this.ns.success(`Authentifizierung fuer ${a.name} erfolgreich`),
      error: () => this.ns.error(`Authentifizierung fuer ${a.name} fehlgeschlagen (401?)`)
    });
  }
  remove(a: AgentEntry) { this.system.removeConfiguredAgent(a.name); this.refresh(); }
  openTerminal(a: any) {
    const mode = a?._terminalMode || 'interactive';
    this.router.navigate(['/panel', a.name], { queryParams: { tab: 'terminal', mode } });
  }

  agentScopeLabel(agent: AgentEntry): string {
    const url = String(agent.url || '').trim().toLowerCase();
    if (!url) return '(unbekannt)';
    const isLocal = url.includes('127.0.0.1') || url.includes('localhost');
    return isLocal ? '(intern)' : '(remote)';
  }

  private agentSortKey(agent: AgentEntry): string {
    const name = String(agent.name || '').trim().toLowerCase();
    if (name === 'hub') return '0-hub';
    if (name === 'worker') return '1-worker';
    return `2-${name}`;
  }
  ping(a: any) {
    this.system.health(a.url).subscribe({ 
      next: () => {
        a._health = 'ok';
        this.ns.success(`${a.name} ist gesund (Health OK)`);
      }, 
      error: (err) => {
        a._health = 'error';
        this.ns.error(`Health-Check fehlgeschlagen fuer ${a.name}`);
      } 
    });
    this.system.ready(a.url).subscribe({ 
      next: (res) => {
        const isReady = !!res?.ready;
        a._db = isReady ? 'Ready' : 'Not Ready';
        if (isReady) {
          this.ns.success(`${a.name} ist bereit (Ready OK)`);
        } else {
          this.ns.error(`${a.name} ist nicht bereit`);
        }
      },
      error: () => {
        a._db = 'DB Offline';
        this.ns.error(`${a.name} ist nicht bereit (Ready Check failed)`);
      }
    });
  }
}
