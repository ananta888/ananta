import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Subscription, interval } from 'rxjs';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
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
  readonly controlPlane = inject(ControlPlaneFacade);
  hub = this.dir.list().find((a) => a.role === 'hub');
  rm: any = null;
  rmLoading = false;
  newTask = { title: '', description: '', priority: 'medium', source: 'ui' };
  autoPlannerStatus: any = null;
  autoPlannerLoading = false;
  autoPlannerRecentGoals: any[] = [];
  showArtifactFlowDetails = false;
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
}
