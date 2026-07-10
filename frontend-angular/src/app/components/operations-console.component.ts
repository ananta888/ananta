import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Subscription, interval } from 'rxjs';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { OpsApiClient, ComposeProjectSummary, DockerContainerSummary, DockerEngineStatus, GitStatus } from '../services/ops-api.client';
import { ControlPlaneFacade } from '../features/control-plane/control-plane.facade';
import { UiSkeletonComponent } from './ui-skeleton.component';

@Component({
  standalone: true,
  selector: 'app-operations-console',
  imports: [CommonModule, FormsModule, RouterLink, UiSkeletonComponent],
  template: `
    <h2>Operations Konsole</h2>
    <p class="muted">Zentrale Steuerung fuer orchestrierte Task-Abarbeitung aus UI und Agenten.</p>
    @if (!hub) {
      <div class="state-banner error">Kein Hub-Agent vorhanden.</div>
    }
    @if (hub) {
      <div class="row flex-between">
        <button (click)="reload()">Aktualisieren</button>
        <span class="muted">Hub: {{ hub.url }} | Live Sync: {{ controlPlane.systemStreamConnected() ? 'connected' : 'idle' }}</span>
      </div>
      @if (rmLoading) {
        <app-ui-skeleton [count]="4" [columns]="4" [lineCount]="2"></app-ui-skeleton>
      } @else {
        <div class="grid cols-4">
          <div class="card"><div class="muted">Offen</div><strong>{{ rm?.queue?.todo || 0 }}</strong></div>
          <div class="card"><div class="muted">Zugewiesen</div><strong>{{ rm?.queue?.assigned || 0 }}</strong></div>
          <div class="card"><div class="muted">In Bearbeitung</div><strong>{{ rm?.queue?.in_progress || 0 }}</strong></div>
          <div class="card"><div class="muted">Fehlgeschlagen</div><strong class="danger">{{ rm?.queue?.failed || 0 }}</strong></div>
        </div>
      }

      <div class="card card-mt">
        <h3>Task-Aufnahme</h3>
        <div class="grid cols-3">
          <label>Title <input [(ngModel)]="newTask.title" /></label>
          <label>Priority
            <select [(ngModel)]="newTask.priority">
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </label>
          <label>Source
            <select [(ngModel)]="newTask.source">
              <option value="ui">ui</option>
              <option value="agent">agent</option>
            </select>
          </label>
        </div>
        <label>Description <textarea rows="3" [(ngModel)]="newTask.description"></textarea></label>
        <div class="row">
          <button (click)="ingest()" [disabled]="!newTask.description.trim()">In zentrale Queue einstellen</button>
        </div>
      </div>

      <div class="card card-mt card-purple-accent">
        <div class="row flex-between">
          <h3 class="no-margin">Auto-Planner Aktivitaet</h3>
          <div class="row gap-md">
            <button class="secondary btn-xs" [routerLink]="['/auto-planner']">Konfigurieren</button>
            <button class="secondary btn-xs" (click)="reloadAutoPlanner()">Aktualisieren</button>
          </div>
        </div>
        @if (autoPlannerLoading) {
          <div class="mt-sm">
            <app-ui-skeleton [count]="1" [lineCount]="4"></app-ui-skeleton>
          </div>
        }
        @if (autoPlannerStatus) {
          <div class="grid cols-4 mt-sm">
            <div>
              <div class="muted">Status</div>
              <strong [class.success]="autoPlannerStatus.enabled" [class.danger]="!autoPlannerStatus.enabled">{{ autoPlannerStatus.enabled ? 'Aktiv' : 'Inaktiv' }}</strong>
            </div>
            <div>
              <div class="muted">Goals verarbeitet</div>
              <strong>{{ autoPlannerStatus.stats?.goals_processed || 0 }}</strong>
            </div>
            <div>
              <div class="muted">Tasks erstellt</div>
              <strong>{{ autoPlannerStatus.stats?.tasks_created || 0 }}</strong>
            </div>
            <div>
              <div class="muted">Follow-ups</div>
              <strong>{{ autoPlannerStatus.stats?.followups_created || 0 }}</strong>
            </div>
          </div>
          @if (autoPlannerStatus.stats?.errors > 0) {
            <div class="error-banner">
              <strong class="danger">Fehler: {{ autoPlannerStatus.stats.errors }}</strong>
            </div>
          }
        }
        @if (autoPlannerRecentGoals.length) {
          <div class="mt-md">
            <h4 class="h4-no-margin">Kuerzliche Goals</h4>
            <div class="goal-list">
              @for (goal of autoPlannerRecentGoals; track goal.id) {
                <div class="goal-item">
                  <div class="row flex-between">
                    <strong class="goal-title">{{ goal.goal?.slice(0, 60) }}{{ goal.goal?.length > 60 ? '...' : '' }}</strong>
                    <span class="muted status-text-sm">{{ goal.tasks_count || 0 }} Tasks</span>
                  </div>
                  @if (goal.created_at) {
                    <div class="muted status-text-sm">
                      {{ goal.created_at * 1000 | date:'dd.MM. HH:mm' }}
                    </div>
                  }
                </div>
              }
            </div>
          </div>
        } @else if (autoPlannerStatus) {
          <div class="muted mt-sm font-sm">Noch keine Goals verarbeitet.</div>
        }
      </div>

      <div class="card card-mt">
        <div class="row flex-between">
          <h3 class="no-margin">Ops Control Surface</h3>
          <button class="button-outline btn-xs" (click)="reloadOps()">Ops aktualisieren</button>
        </div>
        <div class="ops-tabs mt-sm" role="tablist">
          <button type="button" [class.active]="opsTab === 'git'" (click)="opsTab = 'git'">Git</button>
          <button type="button" [class.active]="opsTab === 'docker'" (click)="opsTab = 'docker'">Docker</button>
          <button type="button" [class.active]="opsTab === 'compose'" (click)="opsTab = 'compose'">Compose</button>
        </div>
        @if (opsLoading) {
          <div class="mt-sm"><app-ui-skeleton [count]="1" [lineCount]="4"></app-ui-skeleton></div>
        } @else if (opsError) {
          <div class="state-banner error mt-sm">{{ opsError }}</div>
        } @else {
          @if (opsTab === 'git') {
            @if (gitStatus?.error) {
              <div class="state-banner error mt-sm">{{ gitStatus?.error?.code }}</div>
            } @else if (gitStatus) {
              <div class="grid cols-4 mt-sm">
                <div><div class="muted">Workspace</div><strong>{{ gitStatus.workspace_id }}</strong></div>
                <div><div class="muted">Branch</div><strong>{{ gitStatus.branch || '-' }}</strong></div>
                <div><div class="muted">Upstream</div><strong>{{ gitStatus.upstream || '-' }}</strong></div>
                <div><div class="muted">Dirty</div><strong [class.danger]="gitStatus.dirty">{{ gitStatus.dirty ? 'yes' : 'no' }}</strong></div>
              </div>
              @if (gitStatus.changed_files.length) {
                <div class="table-scroll mt-sm">
                  <table class="table-full">
                    <thead><tr><th>Pfad</th><th>Index</th><th>Worktree</th></tr></thead>
                    <tbody>
                      @for (file of gitStatus.changed_files.slice(0, 20); track file.path) {
                        <tr><td class="font-mono-cell">{{ file.path }}</td><td>{{ file.index_status || '-' }}</td><td>{{ file.worktree_status || '-' }}</td></tr>
                      }
                    </tbody>
                  </table>
                </div>
              } @else {
                <div class="muted mt-sm">Keine Git-Aenderungen.</div>
              }
            }
          }
          @if (opsTab === 'docker') {
            @if (dockerStatus?.error) {
              <div class="state-banner error mt-sm">{{ dockerStatus?.error?.code }}</div>
            }
            @if (dockerStatus) {
              <div class="grid cols-4 mt-sm">
                <div><div class="muted">Boundary</div><strong>{{ dockerStatus.boundary }}</strong></div>
                <div><div class="muted">Engine</div><strong [class.success]="dockerStatus.available">{{ dockerStatus.available ? 'available' : 'unavailable' }}</strong></div>
                <div><div class="muted">Version</div><strong>{{ dockerStatus.docker_version || '-' }}</strong></div>
                <div><div class="muted">Compose</div><strong>{{ dockerStatus.compose_available ? 'yes' : 'no' }}</strong></div>
              </div>
            }
            @if (dockerContainers.length) {
              <div class="table-scroll mt-sm">
                <table class="table-full">
                  <thead><tr><th>Name</th><th>Image</th><th>Status</th><th>Ports</th></tr></thead>
                  <tbody>
                    @for (c of dockerContainers; track c.id) {
                      <tr><td>{{ c.name }}</td><td class="font-mono-cell">{{ c.image }}</td><td>{{ c.status }}</td><td>{{ c.ports || '-' }}</td></tr>
                    }
                  </tbody>
                </table>
              </div>
            } @else {
              <div class="muted mt-sm">Keine Containerdaten verfuegbar.</div>
            }
          }
          @if (opsTab === 'compose') {
            @if (composeProjects.length) {
              <div class="table-scroll mt-sm">
                <table class="table-full">
                  <thead><tr><th>Projekt</th><th>Marker</th><th>Kategorie</th><th>Profiles</th><th>Datei</th></tr></thead>
                  <tbody>
                    @for (project of composeProjects; track project.project_id) {
                      <tr>
                        <td>{{ project.name }}</td>
                        <td>{{ project.marker }}</td>
                        <td>{{ project.category }}</td>
                        <td>{{ project.profiles.join(', ') || '-' }}</td>
                        <td class="font-mono-cell">{{ shortComposeFile(project) }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            } @else {
              <div class="muted mt-sm">Keine registrierten Compose-Projekte.</div>
            }
          }
        }
        <div class="muted font-sm mt-sm">
          Mutierende Ops-Aktionen werden nur ueber Backend-Capabilities, Policy und Approval freigeschaltet.
        </div>
      </div>

      <div class="card card-mt">
        <h3>Letzte Tasks</h3>
        @if (rmLoading) {
          <app-ui-skeleton [count]="1" [lineCount]="5"></app-ui-skeleton>
        } @else {
          <table class="table-full">
            <thead><tr><th>ID</th><th>Status</th><th>Agent</th><th>Bundle-Kontext</th><th>Aktion</th></tr></thead>
            <tbody>
              @for (t of rm?.recent_tasks || []; track t.id) {
                <tr>
                  <td class="font-mono-cell">{{ t.id }}</td>
                  <td>{{ t.status }}</td>
                  <td>{{ t.assigned_agent_url || '-' }}</td>
                  <td>
                    @if (t.context_bundle_summary) {
                      <div class="font-sm">
                        Chunks {{ t.context_bundle_summary.chunk_count || 0 }} · Tokens {{ t.context_bundle_summary.token_estimate || 0 }}
                      </div>
                      <div class="muted font-sm">
                        {{ t.context_bundle_summary.context_policy?.mode || 'n/a' }} · {{ t.context_bundle_summary.context_policy?.window_profile || 'n/a' }}
                      </div>
                      @if (t.context_bundle_summary.why_summary) {
                        <div class="font-sm">{{ t.context_bundle_summary.why_summary }}</div>
                      }
                      @if (topBundleSources(t).length) {
                        <div class="muted font-sm">
                          @for (source of topBundleSources(t); track source.source + '-' + source.engine) {
                            <div>{{ source.engine || 'source' }} · {{ source.source }} · {{ source.score ?? '-' }}</div>
                          }
                        </div>
                      }
                    } @else {
                      <span class="muted">-</span>
                    }
                  </td>
                  <td>
                    <button class="button-outline" (click)="claim(t.id)">Uebernehmen</button>
                    <button class="button-outline" (click)="complete(t.id)">Abschliessen</button>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        }
      </div>
      <div class="card card-mt">
        <div class="row flex-between">
          <h3 class="no-margin">Artifact Flow</h3>
          <button class="button-outline btn-xs" (click)="toggleArtifactFlowDetails()">
            {{ showArtifactFlowDetails ? 'Details ausblenden' : 'Details anzeigen' }}
          </button>
        </div>
        @if (rmLoading) {
          <div class="mt-sm">
            <app-ui-skeleton [count]="1" [lineCount]="4"></app-ui-skeleton>
          </div>
        } @else if (!artifactFlow()) {
          <div class="muted mt-sm">Kein Artifact-Flow Read-Model verfuegbar.</div>
        } @else {
          <div class="grid cols-4 mt-sm">
            <div class="card card-light">
              <div class="muted">Status</div>
              <strong>{{ artifactFlow()?.enabled ? 'enabled' : 'disabled' }}</strong>
            </div>
            <div class="card card-light">
              <div class="muted">Tasks im Flow</div>
              <strong>{{ artifactFlowCount('tasks') }}</strong>
            </div>
            <div class="card card-light">
              <div class="muted">Worker-Jobs</div>
              <strong>{{ artifactFlowCount('worker_jobs') }}</strong>
            </div>
            <div class="card card-light">
              <div class="muted">RAG</div>
              <strong>{{ artifactFlow()?.config?.rag_enabled ? 'on' : 'off' }}</strong>
              <div class="muted status-text-sm">Top-K {{ artifactFlow()?.config?.rag_top_k || '-' }}</div>
            </div>
          </div>
          <div class="muted font-sm mt-sm">
            Max Tasks: {{ artifactFlow()?.config?.max_tasks || '-' }}
            · Max Jobs/Task: {{ artifactFlow()?.config?.max_worker_jobs_per_task || '-' }}
            · Include Content: {{ artifactFlow()?.config?.rag_include_content ? 'yes' : 'no' }}
          </div>
          @if (showArtifactFlowDetails) {
            <div class="table-scroll mt-sm">
              <table class="table-full">
                <thead>
                  <tr><th>Task</th><th>Status</th><th>Sent</th><th>Returned</th><th>Jobs</th><th>RAG</th></tr>
                </thead>
                <tbody>
                  @for (item of artifactFlowItems(); track item.task_id) {
                    <tr>
                      <td class="font-mono-cell">{{ item.task_id }}</td>
                      <td>{{ item.status || '-' }}</td>
                      <td>{{ (item.sent_artifact_ids || []).length }}</td>
                      <td>{{ (item.returned_artifact_ids || []).length }}</td>
                      <td>{{ (item.worker_jobs || []).length }}</td>
                      <td>{{ (item.rag_context || []).length }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        }
      </div>
    }
  `,
})
export class OperationsConsoleComponent implements OnInit, OnDestroy {
  private dir = inject(AgentDirectoryService);
  private ns = inject(NotificationService);
  private opsApi = inject(OpsApiClient);
  readonly controlPlane = inject(ControlPlaneFacade);
  hub = this.dir.list().find((a) => a.role === 'hub');
  rm: any = null;
  rmLoading = false;
  newTask = { title: '', description: '', priority: 'medium', source: 'ui' };
  autoPlannerStatus: any = null;
  autoPlannerLoading = false;
  autoPlannerRecentGoals: any[] = [];
  showArtifactFlowDetails = false;
  opsTab: 'git' | 'docker' | 'compose' = 'git';
  opsLoading = false;
  opsError = '';
  gitStatus: GitStatus | null = null;
  dockerStatus: DockerEngineStatus | null = null;
  dockerContainers: DockerContainerSummary[] = [];
  composeProjects: ComposeProjectSummary[] = [];
  private refreshSub?: Subscription;

  ngOnInit() {
    if (this.hub?.url) {
      this.controlPlane.ensureSystemEvents(this.hub.url);
    }
    this.reload();
    this.refreshSub = interval(10000).subscribe(() => this.reload());
  }

  ngOnDestroy() {
    this.refreshSub?.unsubscribe();
  }

  reload() {
    if (!this.hub) return;
    this.rmLoading = true;
    this.controlPlane.getTaskOrchestrationReadModel(this.hub.url).subscribe({
      next: (r) => (this.rm = r),
      error: () => {
        this.rmLoading = false;
        this.ns.error('Read-model konnte nicht geladen werden');
      },
      complete: () => {
        this.rmLoading = false;
      },
    });
    this.reloadAutoPlanner();
    this.reloadOps();
  }

  reloadOps() {
    if (!this.hub) return;
    this.opsLoading = true;
    this.opsError = '';
    let pending = 4;
    const done = () => {
      pending -= 1;
      if (pending <= 0) this.opsLoading = false;
    };
    this.opsApi.getGitStatus(this.hub.url).subscribe({
      next: (status) => (this.gitStatus = status),
      error: (err) => {
        this.gitStatus = null;
        this.opsError = this.opsError || err?.error?.message || 'Ops Git Status konnte nicht geladen werden';
        done();
      },
      complete: done,
    });
    this.opsApi.getDockerStatus(this.hub.url).subscribe({
      next: (status) => (this.dockerStatus = status),
      error: () => {
        this.dockerStatus = null;
        done();
      },
      complete: done,
    });
    this.opsApi.listDockerContainers(this.hub.url).subscribe({
      next: (data) => (this.dockerContainers = Array.isArray(data?.items) ? data.items : []),
      error: () => {
        this.dockerContainers = [];
        done();
      },
      complete: done,
    });
    this.opsApi.listComposeProjects(this.hub.url).subscribe({
      next: (data) => (this.composeProjects = Array.isArray(data?.items) ? data.items : []),
      error: () => {
        this.composeProjects = [];
        done();
      },
      complete: done,
    });
  }

  reloadAutoPlanner() {
    if (!this.hub) return;
    this.autoPlannerLoading = true;
    this.controlPlane.getAutopilotStatus(this.hub.url).subscribe({
      next: (status) => {
        this.autoPlannerStatus = status;
        this.autoPlannerRecentGoals = Array.isArray(status?.recent_goals) ? status.recent_goals : [];
        this.autoPlannerLoading = false;
      },
      error: () => {
        this.autoPlannerLoading = false;
        this.autoPlannerStatus = null;
        this.autoPlannerRecentGoals = [];
      }
    });
  }

  ingest() {
    if (!this.hub) return;
    this.controlPlane.ingestOrchestrationTask(this.hub.url, { ...this.newTask, created_by: 'ui-operator' }).subscribe({
      next: () => {
        this.ns.success('Task in zentraler Queue erstellt');
        this.newTask = { title: '', description: '', priority: 'medium', source: 'ui' };
        this.reload();
      },
      error: () => this.ns.error('Task konnte nicht erstellt werden'),
    });
  }

  claim(taskId: string) {
    if (!this.hub) return;
    this.controlPlane.claimOrchestrationTask(this.hub.url, { task_id: taskId, agent_url: this.hub.url, lease_seconds: 120 }).subscribe({
      next: () => this.reload(),
      error: () => this.ns.error('Claim fehlgeschlagen'),
    });
  }

  complete(taskId: string) {
    if (!this.hub) return;
    this.controlPlane.completeOrchestrationTask(this.hub.url, { task_id: taskId, actor: 'ui-operator', gate_results: { passed: true } }).subscribe({
      next: () => this.reload(),
      error: () => this.ns.error('Complete fehlgeschlagen'),
    });
  }

  artifactFlow(): any | null {
    const flow = this.rm?.artifact_flow;
    return flow && typeof flow === 'object' ? flow : null;
  }

  artifactFlowItems(): any[] {
    const items = this.artifactFlow()?.items;
    return Array.isArray(items) ? items.slice(0, 30) : [];
  }

  artifactFlowCount(key: 'tasks' | 'worker_jobs' | 'worker_results' | 'memory_entries'): number {
    const value = Number(this.artifactFlow()?.counts?.[key] || 0);
    return Number.isFinite(value) ? value : 0;
  }

  toggleArtifactFlowDetails() {
    this.showArtifactFlowDetails = !this.showArtifactFlowDetails;
  }

  topBundleSources(task: any): any[] {
    const summary = task?.context_bundle_summary || {};
    const explainabilitySources = Array.isArray(summary?.top_sources) ? summary.top_sources : [];
    if (explainabilitySources.length) return explainabilitySources.slice(0, 2);
    const whySources = Array.isArray(summary?.why_top_sources) ? summary.why_top_sources : [];
    return whySources.slice(0, 2);
  }

  shortComposeFile(project: ComposeProjectSummary): string {
    const first = project.compose_files?.[0] || '';
    return first.split('/').slice(-3).join('/');
  }
}
