import { Component, OnInit, OnDestroy, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AgentDirectoryService, AgentEntry } from '../services/agent-directory.service';
import { AgentApiService } from '../services/agent-api.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
import { interval, Subscription } from 'rxjs';

@Component({
  standalone: true,
  selector: 'app-agents-list',
  imports: [FormsModule, RouterLink],
  template: `
    <div class="row" style="justify-content: space-between; align-items: flex-end;">
      <div>
        <h2>Agents</h2>
        <p class="muted">Verwalten Sie Ihre Agent-Instanzen (Hub & Worker).</p>
      </div>
      <div class="row">
        <label class="row" style="gap: 4px; font-size: 13px;">
          Polling (s):
          <input type="number" [(ngModel)]="refreshInterval" (change)="startPolling()" style="width: 45px; padding: 2px 4px;">
        </label>
        <button (click)="add()">Neu</button>
        @if (loading) {
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
                [class.online]="a['_status']==='online'"
                [class.offline]="a['_status']==='offline'"
              [title]="a['_status'] || 'unbekannt'"></div>
              <strong>{{a.name}}</strong>
              <span class="muted">({{a.role || 'worker'}})</span>
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
              <option value="interactive">interactive</option>
              <option value="read">read-only</option>
            </select>
            <button class="button-outline" (click)="openTerminal(a)">Terminal öffnen</button>
            @if (!loading) {
              <span [class.success]="a['_health']==='ok'" [class.danger]="a['_health'] && a['_health']!=='ok'">{{a['_health']||''}}</span>
              <span style="margin-left:8px" [class.success]="a['_db']==='DB OK'" [class.danger]="a['_db'] && a['_db']!=='DB OK'">{{a['_db']||''}}</span>
            } @else {
              <div class="skeleton pill"></div>
              <div class="skeleton pill"></div>
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
              <button (click)="remove(a)" class="danger">Löschen</button>
            </div>
          </details>
        </div>
      }
    </div>
    `
})
export class AgentsListComponent implements OnInit, OnDestroy {
  private dir = inject(AgentDirectoryService);
  private api = inject(AgentApiService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);
  private router = inject(Router);

  agents: (AgentEntry & { _health?: string, _status?: string, _db?: string, _terminalMode?: 'interactive' | 'read' })[] = [];
  private sub?: Subscription;
  refreshInterval = 30;
  loading = false;

  constructor() {
    this.refresh();
  }

  ngOnInit() {
    this.startPolling();
  }

  startPolling() {
    this.sub?.unsubscribe();
    this.sub = interval(this.refreshInterval * 1000).subscribe(() => this.updateBackendStatuses());
    this.updateBackendStatuses();
  }

  ngOnDestroy() {
    this.sub?.unsubscribe();
  }

  refresh() { 
    this.agents = this.dir.list().map(a => ({ ...a, _terminalMode: 'interactive' })) as any;
    this.updateBackendStatuses();
  }

  updateBackendStatuses() {
    const hub = this.agents.find(a => a.role === 'hub');
    if (!hub) return;

    this.loading = true;
    this.hubApi.listAgents(hub.url).subscribe({
      next: (agentMap: any) => {
        this.loading = false;
        if (!agentMap || typeof agentMap !== 'object') return;
        // agentMap ist { name: { status: 'online', ... } }
        this.agents.forEach(a => {
          if (agentMap[a.name]) {
            a['_status'] = agentMap[a.name].status;
          } else if (a.name === hub.name) {
            a['_status'] = 'online';
          }
        });
      },
      error: () => {
        this.loading = false;
        if (hub) {
          hub['_status'] = 'offline';
        }
      }
    });
  }

  add() {
    const idx = this.agents.length + 1;
    const entry: AgentEntry = { name: `agent-${idx}`, url: 'http://localhost:5003', role: 'worker' };
    this.dir.upsert(entry); this.refresh();
  }
  save(a: AgentEntry) { this.dir.upsert(a); this.refresh(); }
  testAuth(a: AgentEntry) {
    this.api.getConfig(a.url).subscribe({
      next: () => this.ns.success(`Authentifizierung für ${a.name} erfolgreich`),
      error: () => this.ns.error(`Authentifizierung für ${a.name} fehlgeschlagen (401?)`)
    });
  }
  remove(a: AgentEntry) { this.dir.remove(a.name); this.refresh(); }
  openTerminal(a: any) {
    const mode = a?._terminalMode || 'interactive';
    this.router.navigate(['/panel', a.name], { queryParams: { tab: 'terminal', mode } });
  }
  ping(a: any) {
    this.api.health(a.url).subscribe({ 
      next: () => {
        a._health = 'ok';
        this.ns.success(`${a.name} ist gesund (Health OK)`);
      }, 
      error: (err) => {
        a._health = 'error';
        this.ns.error(`Health-Check fehlgeschlagen für ${a.name}`);
      } 
    });
    this.api.ready(a.url).subscribe({ 
      next: (res) => {
        a._db = res?.checks?.database?.status === 'ok' ? 'DB OK' : 'DB Error';
        if (a._db === 'DB OK') {
          this.ns.success(`${a.name} Datenbank ist bereit`);
        } else {
          this.ns.error(`${a.name} Datenbankfehler`);
        }
      },
      error: () => {
        a._db = 'DB Offline';
        this.ns.error(`${a.name} ist nicht bereit (Ready Check failed)`);
      }
    });
  }
}
