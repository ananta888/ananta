import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { interval, Subscription } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-dashboard',
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <h2>System Dashboard</h2>
    <p class="muted">Zentrale Uebersicht ueber Agenten und Tasks.</p>
    @if (stats) {
      <div class="grid cols-5">
        <div class="card">
          <h3>Agenten</h3>
          <div class="row" style="justify-content: space-between;">
            <span>Gesamt:</span>
            <strong>{{stats.agents?.total || 0}}</strong>
          </div>
          <div class="row" style="justify-content: space-between;">
            <span>Online:</span>
            <strong class="success">{{stats.agents?.online || 0}}</strong>
          </div>
          <div class="row" style="justify-content: space-between;">
            <span>Offline:</span>
            <strong class="danger">{{stats.agents?.offline || 0}}</strong>
          </div>
        </div>
        <div class="card">
          <h3>Tasks</h3>
          <div class="row" style="justify-content: space-between;">
            <span>Gesamt:</span>
            <strong>{{stats.tasks?.total || 0}}</strong>
          </div>
          <div class="row" style="justify-content: space-between;">
            <span>Abgeschlossen:</span>
            <strong class="success">{{stats.tasks?.completed || 0}}</strong>
          </div>
          <div class="row" style="justify-content: space-between;">
            <span>Fehlgeschlagen:</span>
            <strong class="danger">{{stats.tasks?.failed || 0}}</strong>
          </div>
          <div class="row" style="justify-content: space-between;">
            <span>In Arbeit:</span>
            <strong>{{stats.tasks?.in_progress || 0}}</strong>
          </div>
        </div>
        <div class="card">
          <h3>Shell Pool</h3>
          <div class="row" style="justify-content: space-between;">
            <span>Gesamt:</span>
            <strong>{{stats.shell_pool?.total || 0}}</strong>
          </div>
          <div class="row" style="justify-content: space-between;">
            <span>Frei:</span>
            <strong class="success">{{stats.shell_pool?.free || 0}}</strong>
          </div>
          <div class="row" style="justify-content: space-between;">
            <span>Belegt:</span>
            <strong [class.danger]="stats.shell_pool?.busy > 0">{{stats.shell_pool?.busy || 0}}</strong>
          </div>
        </div>
        @if (stats?.resources) {
          <div class="card">
            <h3>Ressourcen</h3>
            <div class="row" style="justify-content: space-between;">
              <span>CPU:</span>
              <strong>{{stats.resources?.cpu_percent | number:'1.1-1'}}%</strong>
            </div>
            <div class="row" style="justify-content: space-between;">
              <span>RAM:</span>
              <strong>{{(stats.resources?.ram_bytes || 0) / 1024 / 1024 | number:'1.0-0'}} MB</strong>
            </div>
            <div style="margin-top: 8px; background: #eee; height: 4px; border-radius: 2px; overflow: hidden;" role="progressbar" [attr.aria-valuenow]="stats.resources?.cpu_percent || 0" aria-valuemin="0" aria-valuemax="100" [attr.aria-label]="'CPU Auslastung: ' + (stats.resources?.cpu_percent || 0) + ' Prozent'">
              <div [style.width.%]="stats.resources?.cpu_percent || 0" [class.bg-danger]="(stats.resources?.cpu_percent || 0) > 80" [class.bg-success]="(stats.resources?.cpu_percent || 0) <= 80" style="height: 100%;"></div>
            </div>
          </div>
        }
        <div class="card">
          <h3>System Status</h3>
          <div class="row" style="align-items: center; gap: 8px;">
            <div class="status-dot" [class.online]="(stats.agents?.online || 0) > 0" [class.offline]="(stats.agents?.online || 0) === 0" role="status" [attr.aria-label]="(stats.agents?.online || 0) > 0 ? 'System online' : 'System offline'"></div>
            <strong>{{(stats.agents?.online || 0) > 0 ? 'Betriebsbereit' : 'Eingeschraenkt'}}</strong>
          </div>
          @if (activeTeam) {
            <div class="muted" style="font-size: 12px; margin-top: 10px;">
              Aktives Team: <strong>{{activeTeam.name}}</strong> ({{activeTeam.members?.length || 0}} Agenten)
              @if (activeTeam.members?.length) {
                <div style="margin-top: 6px;">
                  @for (m of activeTeam.members; track m) {
                    <div style="font-size: 11px;">
                      {{m.agent_url}} - {{ getRoleName(m.role_id) }}
                    </div>
                  }
                </div>
              }
            </div>
          }
          @if (!activeTeam) {
            <div class="muted" style="font-size: 12px; margin-top: 10px;">
              Kein Team aktiv.
            </div>
          }
          <div class="muted" style="font-size: 11px; margin-top: 5px;">
            Hub: {{stats.agent_name}}<br>
            Letztes Update: {{stats.timestamp * 1000 | date:'HH:mm:ss'}}
          </div>
          <div style="margin-top: 15px;">
            <button [routerLink]="['/board']" style="width: 100%;">Zum Task-Board</button>
          </div>
        </div>
      </div>
    }

    @if (hub) {
      <div class="card" style="margin-top: 14px;">
        <h3>Autopilot Control Center</h3>
        <p class="muted" style="margin-top: 4px;">Steuerung fuer den kontinuierlichen Scrum-Team-Lauf.</p>

        <div class="grid cols-2" style="margin-top: 10px;">
          <label>
            Sprint Goal
            <input [(ngModel)]="autopilotGoal" placeholder="z.B. MVP Login + Team Setup" />
          </label>
          <label>
            Team
            <select [(ngModel)]="autopilotTeamId">
              <option value="">Aktives Team</option>
              @for (t of teamsList; track t) {
                <option [value]="t.id">{{ t.name }}</option>
              }
            </select>
          </label>
          <label>
            Tick-Intervall (s)
            <input type="number" min="3" [(ngModel)]="autopilotIntervalSeconds" />
          </label>
          <label>
            Max Parallelitaet
            <input type="number" min="1" [(ngModel)]="autopilotMaxConcurrency" />
          </label>
          <label>
            Budget-Hinweis
            <input [(ngModel)]="autopilotBudgetLabel" placeholder="z.B. 2h / 10k tokens" />
          </label>
          <label>
            Sicherheitslevel
            <select [(ngModel)]="autopilotSecurityLevel">
              <option value="safe">safe</option>
              <option value="balanced">balanced</option>
              <option value="aggressive">aggressive</option>
            </select>
          </label>
        </div>

        <div class="row" style="gap: 8px; margin-top: 12px;">
          <button (click)="startAutopilot()" [disabled]="autopilotBusy">Start</button>
          <button class="secondary" (click)="stopAutopilot()" [disabled]="autopilotBusy">Stop</button>
          <button class="secondary" (click)="tickAutopilot()" [disabled]="autopilotBusy">Tick now</button>
          <button class="secondary" (click)="refreshAutopilot()" [disabled]="autopilotBusy">Refresh status</button>
        </div>

        @if (autopilotStatus) {
          <div class="grid cols-4" style="margin-top: 12px;">
            <div>
              <div class="muted">Status</div>
              <strong [class.success]="autopilotStatus.running" [class.danger]="!autopilotStatus.running">{{ autopilotStatus.running ? 'running' : 'stopped' }}</strong>
            </div>
            <div>
              <div class="muted">Ticks</div>
              <strong>{{ autopilotStatus.tick_count || 0 }}</strong>
            </div>
            <div>
              <div class="muted">Dispatched</div>
              <strong>{{ autopilotStatus.dispatched_count || 0 }}</strong>
            </div>
            <div>
              <div class="muted">Completed/Failed</div>
              <strong>{{ autopilotStatus.completed_count || 0 }}/{{ autopilotStatus.failed_count || 0 }}</strong>
            </div>
          </div>
          <div class="muted" style="font-size: 11px; margin-top: 8px;">
            Last tick: {{ autopilotStatus.last_tick_at ? (autopilotStatus.last_tick_at * 1000 | date:'HH:mm:ss') : '-' }} |
            Last error: {{ autopilotStatus.last_error || '-' }}
          </div>
        }
      </div>
    }

    @if (history.length > 1) {
      <div class="grid cols-2">
        <div class="card">
          <h3>Task-Erfolgsrate</h3>
          <div style="height: 100px; width: 100%; border-bottom: 1px solid #ccc; border-left: 1px solid #ccc; position: relative; margin-top: 10px;">
            <svg width="100%" height="100%" viewBox="0 0 1000 100" preserveAspectRatio="none" role="img" aria-label="Diagramm der Task-Erfolgsrate ueber Zeit">
              <polyline fill="none" stroke="#28a745" stroke-width="3" [attr.points]="getPoints('completed')" />
              <polyline fill="none" stroke="#dc3545" stroke-width="3" [attr.points]="getPoints('failed')" />
            </svg>
          </div>
          <div style="margin-top: 5px; display: flex; gap: 15px; font-size: 11px;">
            <span style="color: #28a745">- Abgeschlossen</span>
            <span style="color: #dc3545">- Fehlgeschlagen</span>
          </div>
        </div>
        <div class="card">
          <h3>Ressourcen-Auslastung (Hub)</h3>
          <div style="height: 100px; width: 100%; border-bottom: 1px solid #ccc; border-left: 1px solid #ccc; position: relative; margin-top: 10px;">
            <svg width="100%" height="100%" viewBox="0 0 1000 100" preserveAspectRatio="none" role="img" aria-label="Diagramm der Ressourcen-Auslastung ueber Zeit">
              <polyline fill="none" stroke="#007bff" stroke-width="3" [attr.points]="getPoints('cpu')" />
              <polyline fill="none" stroke="#ffc107" stroke-width="3" [attr.points]="getPoints('ram')" />
            </svg>
          </div>
          <div style="margin-top: 5px; display: flex; gap: 15px; font-size: 11px;">
            <span style="color: #007bff">- CPU (%)</span>
            <span style="color: #ffc107">- RAM</span>
          </div>
        </div>
      </div>
    }

    @if (agentsList.length > 0) {
      <div class="card">
        <h3>Agenten Status</h3>
        <div class="grid cols-4">
          @for (agent of agentsList; track agent) {
            <div style="padding: 8px; border: 1px solid #eee; border-radius: 4px;">
              <div class="row" style="gap: 8px; align-items: center;">
                <div class="status-dot" [class.online]="agent.status === 'online'" [class.offline]="agent.status !== 'online'" role="status" [attr.aria-label]="agent.name + ' ist ' + (agent.status === 'online' ? 'online' : 'offline')"></div>
                <span style="font-weight: 500;">{{agent.name}}</span>
                <span class="muted" style="font-size: 11px;">{{agent.role}}</span>
              </div>
              @if (agent.resources) {
                <div class="muted" style="font-size: 11px; margin-top: 5px; display: flex; justify-content: space-between;">
                  <span>CPU: {{agent.resources.cpu_percent | number:'1.0-1'}}%</span>
                  <span>RAM: {{agent.resources.ram_bytes / 1024 / 1024 | number:'1.0-0'}} MB</span>
                </div>
              }
            </div>
          }
        </div>
      </div>
    }

    @if (!stats && hub) {
      <div class="card">
        <p>Lade Statistiken von Hub ({{hub.url}})...</p>
      </div>
    }

    @if (!hub) {
      <div class="card danger">
        <p>Kein Hub-Agent konfiguriert. Bitte fuegen Sie einen Agenten mit der Rolle "hub" hinzu.</p>
        <button [routerLink]="['/agents']">Agenten verwalten</button>
      </div>
    }
  `
})
export class DashboardComponent implements OnInit, OnDestroy {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);

  hub = this.dir.list().find(a => a.role === 'hub');
  stats: any;
  history: any[] = [];
  agentsList: any[] = [];
  teamsList: any[] = [];
  activeTeam: any;
  roles: any[] = [];
  autopilotStatus: any;
  autopilotBusy = false;
  autopilotGoal = '';
  autopilotTeamId = '';
  autopilotIntervalSeconds = 20;
  autopilotMaxConcurrency = 2;
  autopilotBudgetLabel = '';
  autopilotSecurityLevel: 'safe' | 'balanced' | 'aggressive' = 'safe';
  private sub?: Subscription;

  ngOnInit() {
    this.refresh();
    this.sub = interval(10000).subscribe(() => this.refresh());
  }

  ngOnDestroy() {
    this.sub?.unsubscribe();
  }

  refresh() {
    if (!this.hub) {
      this.hub = this.dir.list().find(a => a.role === 'hub');
    }
    if (!this.hub) return;

    this.hubApi.getStats(this.hub.url).subscribe({
      next: s => this.stats = (s && typeof s === 'object') ? s : undefined,
      error: () => this.ns.error('Dashboard-Statistiken konnten nicht geladen werden')
    });

    this.hubApi.getStatsHistory(this.hub.url).subscribe({
      next: h => this.history = Array.isArray(h) ? h : [],
      error: () => this.ns.error('Dashboard-Historie konnte nicht geladen werden')
    });

    this.hubApi.listTeams(this.hub.url).subscribe({
      next: teams => {
        this.teamsList = Array.isArray(teams) ? teams : [];
        this.activeTeam = this.teamsList.find(t => t.is_active);
      },
      error: () => this.ns.error('Teams konnten nicht geladen werden')
    });

    this.hubApi.listTeamRoles(this.hub.url).subscribe({
      next: roles => this.roles = Array.isArray(roles) ? roles : [],
      error: () => this.ns.error('Team-Rollen konnten nicht geladen werden')
    });

    this.hubApi.listAgents(this.hub.url).subscribe({
      next: agents => {
        if (Array.isArray(agents)) {
          this.agentsList = agents;
        } else if (agents && typeof agents === 'object') {
          this.agentsList = Object.entries(agents).map(([name, info]: [string, any]) => ({
            name: info.name || name,
            ...info
          }));
        } else {
          this.agentsList = [];
        }
      },
      error: () => this.ns.error('Agentenliste konnte nicht geladen werden')
    });

    this.refreshAutopilot();
  }

  refreshAutopilot() {
    if (!this.hub) return;
    this.hubApi.getAutopilotStatus(this.hub.url).subscribe({
      next: s => this.autopilotStatus = s,
      error: () => this.ns.error('Autopilot-Status konnte nicht geladen werden')
    });
  }

  startAutopilot() {
    if (!this.hub) return;
    this.autopilotBusy = true;
    this.hubApi.startAutopilot(this.hub.url, {
      interval_seconds: Number(this.autopilotIntervalSeconds) || 20,
      max_concurrency: Number(this.autopilotMaxConcurrency) || 2
    }).subscribe({
      next: s => {
        this.autopilotStatus = s;
        this.autopilotBusy = false;
      },
      error: () => {
        this.autopilotBusy = false;
        this.ns.error('Autopilot konnte nicht gestartet werden');
      }
    });
  }

  stopAutopilot() {
    if (!this.hub) return;
    this.autopilotBusy = true;
    this.hubApi.stopAutopilot(this.hub.url).subscribe({
      next: s => {
        this.autopilotStatus = s;
        this.autopilotBusy = false;
      },
      error: () => {
        this.autopilotBusy = false;
        this.ns.error('Autopilot konnte nicht gestoppt werden');
      }
    });
  }

  tickAutopilot() {
    if (!this.hub) return;
    this.autopilotBusy = true;
    this.hubApi.tickAutopilot(this.hub.url).subscribe({
      next: s => {
        this.autopilotStatus = s;
        this.autopilotBusy = false;
      },
      error: () => {
        this.autopilotBusy = false;
        this.ns.error('Autopilot-Tick fehlgeschlagen');
      }
    });
  }

  getPoints(type: 'completed' | 'failed' | 'cpu' | 'ram'): string {
    if (this.history.length < 2) return '';

    let maxVal = 1;
    if (type === 'completed' || type === 'failed') {
      maxVal = Math.max(...this.history.map(h => h.tasks?.total || 1), 1);
    } else if (type === 'cpu') {
      maxVal = 100;
    } else if (type === 'ram') {
      maxVal = Math.max(...this.history.map(h => h.resources?.ram_bytes || 1), 1);
    }

    const stepX = 1000 / (this.history.length - 1);

    return this.history.map((h, i) => {
      let val = 0;
      if (type === 'completed' || type === 'failed') {
        val = h.tasks ? h.tasks[type] : 0;
      } else if (type === 'cpu') {
        val = h.resources?.cpu_percent || 0;
      } else if (type === 'ram') {
        val = h.resources?.ram_bytes || 0;
      }
      const x = i * stepX;
      const y = 100 - (val / maxVal * 100);
      return `${x},${y}`;
    }).join(' ');
  }

  getRoleName(roleId: string): string {
    return this.roles.find(r => r.id === roleId)?.name || roleId;
  }
}
