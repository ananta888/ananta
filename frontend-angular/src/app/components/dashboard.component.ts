import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { interval, Subscription } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
import { ToastService } from '../services/toast.service';
import { UiAsyncState } from '../models/ui.models';
import { OnboardingChecklistComponent } from './onboarding-checklist.component';
import { TooltipDirective } from '../directives/tooltip.directive';

@Component({
  standalone: true,
  selector: 'app-dashboard',
  imports: [CommonModule, FormsModule, RouterLink, OnboardingChecklistComponent, TooltipDirective],
  template: `
    <h2>System Dashboard</h2>
    <p class="muted">Zentrale Uebersicht ueber Agenten und Tasks.</p>
    @if (viewState.loading) {
      <div class="card"><div class="skeleton block"></div></div>
    }
    @if (viewState.error) {
      <div class="card danger">{{ viewState.error }}</div>
    }
    @if (!viewState.loading && viewState.empty) {
      <div class="card empty-state">
        <h3>Noch keine Tasks vorhanden</h3>
        <p class="muted">
          Erstellen Sie Ihren ersten Task, um mit der Arbeit zu beginnen.<br>
          Nutzen Sie das Quick Action Goal oben oder navigieren Sie zum Board.
        </p>
        <button class="primary" [routerLink]="['/board']">Zum Board</button>
      </div>
    }

    @if (hub) {
      <div class="card card-primary mb-md">
        <h3 class="no-margin">Quick Action: Neues Goal</h3>
        <p class="muted font-sm mt-sm">Beschreibe ein Ziel und lasse automatisch Tasks generieren.</p>
        <div class="row gap-sm mt-sm flex-end">
          <div class="flex-1">
            <label class="label-no-margin">
              <input
                [(ngModel)]="quickGoalText"
                placeholder="z.B. Implementiere User-Login mit JWT-Authentifizierung"
                class="w-full"
                aria-label="Quick Goal Beschreibung eingeben"
              />
            </label>
          </div>
          <button (click)="submitQuickGoal()" [disabled]="quickGoalBusy || !quickGoalText.trim()" aria-label="Goal planen und Tasks generieren">
            @if (quickGoalBusy) {
              Generiere...
            } @else {
              Goal planen
            }
          </button>
          <button class="secondary" [routerLink]="['/auto-planner']" aria-label="Zur Auto-Planner Konfiguration navigieren">Zur Auto-Planner Konfiguration</button>
        </div>
        @if (quickGoalResult) {
          <div class="card-success mt-sm">
            <div class="row space-between">
              <span><strong>{{ quickGoalResult.tasks_created }}</strong> Tasks erstellt</span>
              <button class="secondary btn-small" (click)="goToBoard()">Zum Board</button>
            </div>
            @if (quickGoalResult.task_ids?.length) {
              <div class="muted status-text-sm">
                Task IDs: {{ quickGoalResult.task_ids.slice(0, 3).join(', ') }}{{ quickGoalResult.task_ids.length > 3 ? '...' : '' }}
              </div>
            }
          </div>
        }
      </div>
    }

    @if (stats) {
      <app-onboarding-checklist />
      <div class="grid cols-5">
        <div class="card">
          <h3>Agenten</h3>
          <div class="row space-between">
            <span>Gesamt:</span>
            <strong>{{stats.agents?.total || 0}}</strong>
          </div>
          <div class="row space-between">
            <span>Online:</span>
            <strong class="success">{{stats.agents?.online || 0}}</strong>
          </div>
          <div class="row space-between">
            <span>Offline:</span>
            <strong class="danger">{{stats.agents?.offline || 0}}</strong>
          </div>
        </div>
        <div class="card">
          <h3>Tasks</h3>
          <div class="row space-between">
            <span>Gesamt:</span>
            <strong>{{stats.tasks?.total || 0}}</strong>
          </div>
          <div class="row space-between">
            <span>Abgeschlossen:</span>
            <strong class="success">{{stats.tasks?.completed || 0}}</strong>
          </div>
          <div class="row space-between">
            <span>Fehlgeschlagen:</span>
            <strong class="danger">{{stats.tasks?.failed || 0}}</strong>
          </div>
          <div class="row space-between">
            <span>In Arbeit:</span>
            <strong>{{stats.tasks?.in_progress || 0}}</strong>
          </div>
        </div>
        <div class="card">
          <h3>Shell Pool</h3>
          <div class="row space-between">
            <span>Gesamt:</span>
            <strong>{{stats.shell_pool?.total || 0}}</strong>
          </div>
          <div class="row space-between">
            <span>Frei:</span>
            <strong class="success">{{stats.shell_pool?.free || 0}}</strong>
          </div>
          <div class="row space-between">
            <span>Belegt:</span>
            <strong [class.danger]="stats.shell_pool?.busy > 0">{{stats.shell_pool?.busy || 0}}</strong>
          </div>
        </div>
        @if (stats?.resources) {
          <div class="card">
            <h3>Ressourcen</h3>
            <div class="row space-between">
              <span>CPU:</span>
              <strong>{{stats.resources?.cpu_percent | number:'1.1-1'}}%</strong>
            </div>
            <div class="row space-between">
              <span>RAM:</span>
              <strong>{{(stats.resources?.ram_bytes || 0) / 1024 / 1024 | number:'1.0-0'}} MB</strong>
            </div>
            <div class="progress-bar mt-sm" role="progressbar" [attr.aria-valuenow]="stats.resources?.cpu_percent || 0" aria-valuemin="0" aria-valuemax="100" [attr.aria-label]="'CPU Auslastung: ' + (stats.resources?.cpu_percent || 0) + ' Prozent'">
              <div class="progress-bar-fill" [style.width.%]="stats.resources?.cpu_percent || 0" [class.bg-danger]="(stats.resources?.cpu_percent || 0) > 80" [class.bg-success]="(stats.resources?.cpu_percent || 0) <= 80"></div>
            </div>
          </div>
        }
        <div class="card">
          <h3>System Status</h3>
          <div class="row gap-sm">
            <div class="status-dot" [class.online]="(stats.agents?.online || 0) > 0" [class.offline]="(stats.agents?.online || 0) === 0" role="status" [attr.aria-label]="(stats.agents?.online || 0) > 0 ? 'System online' : 'System offline'"></div>
            <strong>{{(stats.agents?.online || 0) > 0 ? 'Betriebsbereit' : 'Eingeschraenkt'}}</strong>
          </div>
          @if (activeTeam) {
            <div class="muted font-sm mt-md">
              Aktives Team: <strong>{{activeTeam.name}}</strong> ({{activeTeam.members?.length || 0}} Agenten)
              @if (activeTeam.members?.length) {
                <div class="mt-sm">
                  @for (m of activeTeam.members; track m) {
                    <div class="status-text-sm font-sm">
                      {{m.agent_url}} - {{ getRoleName(m.role_id) }}
                    </div>
                  }
                </div>
              }
            </div>
          }
          @if (!activeTeam) {
            <div class="muted font-sm mt-md">
              Kein Team aktiv.
            </div>
          }
          <div class="muted status-text-sm">
            Hub: {{stats.agent_name}}<br>
            Letztes Update: {{stats.timestamp * 1000 | date:'HH:mm:ss'}}
          </div>
          <div class="mt-lg">
            <button [routerLink]="['/board']" class="w-full">Zum Task-Board</button>
          </div>
        </div>
      </div>
    }

    @if (hub) {
      <div class="card mt-md">
        <div class="row space-between">
          <div>
            <h3 class="no-margin">LLM Benchmark & Empfehlung</h3>
            <div class="muted font-sm mt-sm">
              Vergleich je Aufgabenart mit transparenter Bewertungsgrundlage.
            </div>
          </div>
          <div class="row gap-sm">
            <select aria-label="Benchmark Aufgabenart" [(ngModel)]="benchmarkTaskKind" (ngModelChange)="refreshBenchmarks()">
              <option value="analysis">analysis</option>
              <option value="coding">coding</option>
              <option value="doc">doc</option>
              <option value="ops">ops</option>
            </select>
            <button class="secondary" (click)="refreshBenchmarks()" aria-label="Benchmark-Daten aktualisieren">Refresh</button>
          </div>
        </div>
        @if (benchmarkData.length) {
          <div class="grid cols-4 mt-sm">
            <div class="card card-light">
              <div class="muted">Empfohlenes Modell</div>
              <strong>{{ benchmarkData[0]?.provider }} / {{ benchmarkData[0]?.model }}</strong>
              <div class="muted status-text-sm-alt">
                Suitability: {{ benchmarkData[0]?.focus?.suitability_score || 0 | number:'1.0-2' }}%
              </div>
            </div>
            <div class="card card-light">
              <div class="muted">Success Rate</div>
              <strong>{{ benchmarkData[0]?.focus?.success_rate || 0 | percent:'1.0-0' }}</strong>
            </div>
            <div class="card card-light">
              <div class="muted">Quality Rate</div>
              <strong>{{ benchmarkData[0]?.focus?.quality_rate || 0 | percent:'1.0-0' }}</strong>
            </div>
            <div class="card card-light">
              <div class="muted">Letztes Update</div>
              <strong>{{ benchmarkUpdatedAt ? (benchmarkUpdatedAt * 1000 | date:'HH:mm:ss') : '-' }}</strong>
            </div>
          </div>
          <div class="table-scroll mt-sm">
            <table class="standard-table table-min-600">
              <thead>
                <tr class="card-light">
                  <th>Rank</th>
                  <th>Provider</th>
                  <th>Model</th>
                  <th>Suitability</th>
                  <th>Success</th>
                  <th>Quality</th>
                  <th>Latency</th>
                  <th>Tokens</th>
                </tr>
              </thead>
              <tbody>
                @for (item of benchmarkData; track item.id; let i = $index) {
                  <tr>
                    <td>{{ i + 1 }}</td>
                    <td>{{ item.provider }}</td>
                    <td class="font-mono font-sm">{{ item.model }}</td>
                    <td>{{ item.focus?.suitability_score || 0 | number:'1.0-2' }}%</td>
                    <td>{{ item.focus?.success_rate || 0 | percent:'1.0-0' }}</td>
                    <td>{{ item.focus?.quality_rate || 0 | percent:'1.0-0' }}</td>
                    <td>{{ item.focus?.avg_latency_ms || 0 | number:'1.0-0' }} ms</td>
                    <td>{{ item.focus?.avg_tokens || 0 | number:'1.0-0' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else {
          <div class="muted mt-sm">Noch keine Benchmarkdaten vorhanden.</div>
        }
      </div>
      <div class="card mt-md">
        <h3>Autopilot Control Center <span class="help-icon" [appTooltip]="'Der Autopilot fuehrt Tasks automatisch in regelmaessigen Abstaenden aus.'" tabindex="0">?</span></h3>
        <p class="muted mt-sm">Steuerung fuer den kontinuierlichen Scrum-Team-Lauf.</p>

        <div class="grid cols-2 mt-sm">
          <label>
            Sprint Goal
            <input [(ngModel)]="autopilotGoal" placeholder="z.B. MVP Login + Team Setup" aria-label="Autopilot Sprint Goal" />
          </label>
          <label>
            Team
            <select [(ngModel)]="autopilotTeamId" aria-label="Autopilot Team auswaehlen">
              <option value="">Aktives Team</option>
              @for (t of teamsList; track t) {
                <option [value]="t.id">{{ t.name }}</option>
              }
            </select>
          </label>
          <label>
            Tick-Intervall (s) <span class="help-icon" [appTooltip]="'Zeit zwischen automatischen Ausfuehrungen in Sekunden.'" tabindex="0">?</span>
            <input type="number" min="3" [(ngModel)]="autopilotIntervalSeconds" aria-label="Autopilot Tick-Intervall in Sekunden" />
          </label>
          <label>
            Max Parallelitaet <span class="help-icon" [appTooltip]="'Maximale Anzahl gleichzeitig ausgefuehrter Tasks.'" tabindex="0">?</span>
            <input type="number" min="1" [(ngModel)]="autopilotMaxConcurrency" aria-label="Autopilot maximale Parallelitaet" />
          </label>
          <label>
            Budget-Hinweis
            <input [(ngModel)]="autopilotBudgetLabel" placeholder="z.B. 2h / 10k tokens" aria-label="Autopilot Budget-Hinweis" />
          </label>
          <label>
            Sicherheitslevel <span class="help-icon" [appTooltip]="'safe: Nur sichere Ops, balanced: Eingeschraenkt, aggressive: Alle Ops erlaubt'" tabindex="0">?</span>
            <select [(ngModel)]="autopilotSecurityLevel" aria-label="Autopilot Sicherheitslevel">
              <option value="safe">safe</option>
              <option value="balanced">balanced</option>
              <option value="aggressive">aggressive</option>
            </select>
          </label>
        </div>

        <div class="row gap-sm mt-md">
          <button (click)="startAutopilot()" [disabled]="autopilotBusy" aria-label="Autopilot starten">Start</button>
          <button class="secondary" (click)="stopAutopilot()" [disabled]="autopilotBusy" aria-label="Autopilot stoppen">Stop</button>
          <button class="secondary" (click)="tickAutopilot()" [disabled]="autopilotBusy" aria-label="Autopilot manuell ticken">Tick now</button>
          <button class="secondary" (click)="refreshAutopilot()" [disabled]="autopilotBusy" aria-label="Autopilot Status aktualisieren">Refresh status</button>
        </div>

        @if (autopilotStatus) {
          <div class="grid cols-4 mt-md">
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
          <div class="muted status-text-sm-lg">
            Last tick: {{ autopilotStatus.last_tick_at ? (autopilotStatus.last_tick_at * 1000 | date:'HH:mm:ss') : '-' }} |
            Last error: {{ autopilotStatus.last_error || '-' }}
          </div>
        }

        <div class="card card-light mt-md">
          <h3 class="no-margin">Live Decision Timeline</h3>
          <div class="grid cols-4 mt-sm">
            <label>
              Team
              <select [(ngModel)]="timelineTeamId" (ngModelChange)="refreshTaskTimeline()" aria-label="Timeline Team-Filter">
                <option value="">Alle</option>
                @for (t of teamsList; track t) {
                  <option [value]="t.id">{{ t.name }}</option>
                }
              </select>
            </label>
            <label>
              Agent
              <select [(ngModel)]="timelineAgent" (ngModelChange)="refreshTaskTimeline()" aria-label="Timeline Agent-Filter">
                <option value="">Alle</option>
                @for (a of agentsList; track a) {
                  <option [value]="a.url">{{ a.name }}</option>
                }
              </select>
            </label>
            <label>
              Status
              <select [(ngModel)]="timelineStatus" (ngModelChange)="refreshTaskTimeline()" aria-label="Timeline Status-Filter">
                <option value="">Alle</option>
                <option value="todo">todo</option>
                <option value="assigned">assigned</option>
                <option value="completed">completed</option>
                <option value="failed">failed</option>
                <option value="blocked">blocked</option>
              </select>
            </label>
            <label class="row gap-sm flex-end">
              <input type="checkbox" [(ngModel)]="timelineErrorOnly" (ngModelChange)="refreshTaskTimeline()" aria-label="Timeline nur Fehler anzeigen" />
              Nur Fehler
            </label>
          </div>
          <div class="muted font-sm mt-sm">Eintraege: {{ taskTimeline.length }}</div>
          <div class="timeline-container mt-sm">
            @for (ev of taskTimeline; track ev) {
              <div class="list-item">
                <div class="row space-between">
                  <div class="row gap-sm">
                    <strong>{{ ev.event_type }}</strong>
                    @if (isGuardrailEvent(ev)) {
                      <span class="badge danger">Guardrail Block</span>
                    }
                  </div>
                  <span class="muted">{{ (ev.timestamp || 0) * 1000 | date:'HH:mm:ss' }}</span>
                </div>
                <div class="muted font-sm">
                  Task: <a [routerLink]="['/task', ev.task_id]">{{ ev.task_id }}</a> |
                  Agent: {{ shortActor(ev.actor) }} |
                  Status: {{ ev.task_status || '-' }}
                </div>
                @if (ev.details?.reason) {
                  <div class="font-sm mt-sm">Grund: {{ ev.details.reason }}</div>
                }
                @if (isGuardrailEvent(ev)) {
                  <div class="font-sm mt-sm">
                    Blockierte Tools: {{ guardrailBlockedToolsCount(ev) }}
                  </div>
                }
                @if (isGuardrailEvent(ev) && guardrailReasonsText(ev)) {
                  <div class="muted font-sm mt-sm">
                    Regeln: {{ guardrailReasonsText(ev) }}
                  </div>
                }
                @if (ev.details?.output_preview) {
                  <div class="muted font-sm mt-sm">Ergebnis: {{ ev.details.output_preview }}</div>
                }
              </div>
            }
            @if (!taskTimeline.length) {
              <div class="list-item muted">Keine Timeline-Eintraege fuer aktuellen Filter.</div>
            }
          </div>
        </div>
      </div>
    }

    @if (history.length > 1) {
      <div class="grid cols-2">
        <div class="card">
          <h3>Task-Erfolgsrate</h3>
          <div class="chart-container mt-sm">
            <svg width="100%" height="100%" viewBox="0 0 1000 100" preserveAspectRatio="none" role="img" aria-label="Diagramm der Task-Erfolgsrate ueber Zeit">
              <polyline fill="none" stroke="#28a745" stroke-width="3" [attr.points]="getPoints('completed')" />
              <polyline fill="none" stroke="#dc3545" stroke-width="3" [attr.points]="getPoints('failed')" />
            </svg>
          </div>
          <div class="chart-legend">
            <span class="chart-legend-color success">- Abgeschlossen</span>
            <span class="chart-legend-color danger">- Fehlgeschlagen</span>
          </div>
        </div>
        <div class="card">
          <h3>Ressourcen-Auslastung (Hub)</h3>
          <div class="chart-container mt-sm">
            <svg width="100%" height="100%" viewBox="0 0 1000 100" preserveAspectRatio="none" role="img" aria-label="Diagramm der Ressourcen-Auslastung ueber Zeit">
              <polyline fill="none" stroke="#007bff" stroke-width="3" [attr.points]="getPoints('cpu')" />
              <polyline fill="none" stroke="#ffc107" stroke-width="3" [attr.points]="getPoints('ram')" />
            </svg>
          </div>
          <div class="chart-legend">
            <span class="chart-legend-color info">- CPU (%)</span>
            <span class="chart-legend-color warning">- RAM</span>
          </div>
        </div>
      </div>
    }

    @if (agentsList.length > 0) {
      <div class="card">
        <h3>Agenten Status</h3>
        <div class="grid cols-4">
          @for (agent of agentsList; track agent) {
            <div class="agent-card">
              <div class="row gap-sm">
                <div class="status-dot" [class.online]="agent.status === 'online'" [class.offline]="agent.status !== 'online'" role="status" [attr.aria-label]="agent.name + ' ist ' + (agent.status === 'online' ? 'online' : 'offline')"></div>
                <span class="font-weight-medium">{{agent.name}}</span>
                <span class="muted font-sm">{{agent.role}}</span>
              </div>
              @if (agent.resources) {
                <div class="muted font-sm mt-sm row space-between">
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
  private toast = inject(ToastService);
  private router = inject(Router);

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
  taskTimeline: any[] = [];
  benchmarkTaskKind: 'coding' | 'analysis' | 'doc' | 'ops' = 'analysis';
  benchmarkData: any[] = [];
  benchmarkUpdatedAt: number | null = null;
  viewState: UiAsyncState = { loading: true, error: null, empty: false };
  timelineTeamId = '';
  timelineAgent = '';
  timelineStatus = '';
  timelineErrorOnly = false;
  quickGoalText = '';
  quickGoalBusy = false;
  quickGoalResult: { tasks_created: number; task_ids: string[] } | null = null;
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

    this.viewState = { loading: true, error: null, empty: false };
    this.hubApi.getDashboardReadModel(this.hub.url).subscribe({
      next: (rm) => {
        const counts = rm?.tasks?.counts || {};
        this.stats = {
          agents: {
            total: Number(rm?.agents?.count || 0),
            online: Array.isArray(rm?.agents?.items) ? rm.agents.items.filter((a: any) => a.status === 'online').length : 0,
            offline: Array.isArray(rm?.agents?.items) ? rm.agents.items.filter((a: any) => a.status !== 'online').length : 0,
          },
          tasks: {
            total: Number(counts.total || 0),
            completed: Number(counts.completed || 0),
            failed: Number(counts.failed || 0),
            in_progress: Number(counts.in_progress || 0),
          },
          timestamp: Number(rm?.context_timestamp || Math.floor(Date.now() / 1000)),
          agent_name: 'hub',
        };
        this.teamsList = Array.isArray(rm?.teams?.items) ? rm.teams.items : [];
        this.roles = Array.isArray(rm?.roles?.items) ? rm.roles.items : [];
        this.agentsList = Array.isArray(rm?.agents?.items) ? rm.agents.items : [];
        this.benchmarkData = Array.isArray(rm?.benchmarks?.items) ? rm.benchmarks.items : [];
        this.benchmarkUpdatedAt = Number(rm?.benchmarks?.updated_at || 0) || null;
        this.activeTeam = this.teamsList.find(t => t.is_active);
        this.taskTimeline = Array.isArray(rm?.tasks?.recent)
          ? rm.tasks.recent.map((t: any) => ({
              event_type: 'task_state',
              task_id: t.task_id,
              task_status: t.status,
              timestamp: t.updated_at || rm?.context_timestamp,
              actor: 'system',
            }))
          : [];
        this.viewState = { loading: false, error: null, empty: !this.stats?.tasks?.total };
      },
      error: () => {
        this.viewState = { loading: false, error: 'Dashboard-Daten konnten nicht geladen werden', empty: false };
        this.ns.error('Dashboard-Daten konnten nicht geladen werden');
      }
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
      next: s => {
        this.autopilotStatus = s;
        this.autopilotGoal = s?.goal || '';
        this.autopilotTeamId = s?.team_id || '';
        this.autopilotBudgetLabel = s?.budget_label || '';
        this.autopilotSecurityLevel = (s?.security_level || 'safe');
      },
      error: () => this.ns.error('Autopilot-Status konnte nicht geladen werden')
    });
  }

  startAutopilot() {
    if (!this.hub) return;
    this.autopilotBusy = true;
    const selectedTeamId = this.autopilotTeamId || this.activeTeam?.id || '';
    this.hubApi.startAutopilot(this.hub.url, {
      interval_seconds: Number(this.autopilotIntervalSeconds) || 20,
      max_concurrency: Number(this.autopilotMaxConcurrency) || 2,
      goal: this.autopilotGoal || '',
      team_id: selectedTeamId,
      budget_label: this.autopilotBudgetLabel || '',
      security_level: this.autopilotSecurityLevel || 'safe'
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

  refreshTaskTimeline() {
    if (!this.hub) return;
    this.hubApi.getTaskTimeline(
      this.hub.url,
      {
        team_id: this.timelineTeamId || undefined,
        agent: this.timelineAgent || undefined,
        status: this.timelineStatus || undefined,
        error_only: this.timelineErrorOnly,
        limit: 150
      }
    ).subscribe({
      next: payload => {
        const items = payload?.items;
        this.taskTimeline = Array.isArray(items) ? items : [];
      },
      error: () => this.ns.error('Task-Timeline konnte nicht geladen werden')
    });
  }

  refreshBenchmarks() {
    if (!this.hub) return;
    this.hubApi.getLlmBenchmarks(this.hub.url, { task_kind: this.benchmarkTaskKind, top_n: 8 }).subscribe({
      next: payload => {
        this.benchmarkData = Array.isArray(payload?.items) ? payload.items : [];
        this.benchmarkUpdatedAt = Number(payload?.updated_at || 0) || null;
      },
      error: () => {
        this.benchmarkData = [];
      }
    });
  }

  shortActor(actor: string): string {
    if (!actor) return 'system';
    const match = this.agentsList.find(a => a.url === actor);
    if (match?.name) return match.name;
    return actor.replace(/^https?:\/\//, '');
  }

  isGuardrailEvent(ev: any): boolean {
    return String(ev?.event_type || '').toLowerCase() === 'tool_guardrail_blocked';
  }

  guardrailBlockedToolsCount(ev: any): number {
    const blockedTools = ev?.details?.blocked_tools;
    return Array.isArray(blockedTools) ? blockedTools.length : 0;
  }

  guardrailReasonsText(ev: any): string {
    const reasons = ev?.details?.blocked_reasons;
    return Array.isArray(reasons) ? reasons.join(', ') : '';
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

  submitQuickGoal() {
    if (!this.hub || !this.quickGoalText.trim()) return;
    this.quickGoalBusy = true;
    this.quickGoalResult = null;

    this.hubApi.planGoal(this.hub.url, {
      goal: this.quickGoalText.trim(),
      create_tasks: true
    }).subscribe({
      next: (result: any) => {
        this.quickGoalBusy = false;
        this.quickGoalResult = {
          tasks_created: result?.created_task_ids?.length || 0,
          task_ids: result?.created_task_ids || []
        };
        this.toast.success(`${this.quickGoalResult.tasks_created} Tasks erstellt`);
        this.quickGoalText = '';
        this.refresh();
      },
      error: () => {
        this.quickGoalBusy = false;
        this.toast.error('Goal-Planung fehlgeschlagen');
      }
    });
  }

  goToBoard() {
    this.router.navigate(['/board']);
  }
}
