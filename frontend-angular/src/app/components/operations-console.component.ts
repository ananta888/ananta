import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-operations-console',
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <h2>Operations Console</h2>
    <p class="muted">Zentrale Steuerung fuer autonome Task-Abarbeitung aus UI und Agenten.</p>
    @if (!hub) {
      <div class="state-banner error">Kein Hub-Agent vorhanden.</div>
    }
    @if (hub) {
      <div class="row" style="justify-content: space-between;">
        <button (click)="reload()">Refresh</button>
        <span class="muted">Hub: {{ hub.url }}</span>
      </div>
      <div class="grid cols-4">
        <div class="card"><div class="muted">Todo</div><strong>{{ rm?.queue?.todo || 0 }}</strong></div>
        <div class="card"><div class="muted">Assigned</div><strong>{{ rm?.queue?.assigned || 0 }}</strong></div>
        <div class="card"><div class="muted">In Progress</div><strong>{{ rm?.queue?.in_progress || 0 }}</strong></div>
        <div class="card"><div class="muted">Failed</div><strong class="danger">{{ rm?.queue?.failed || 0 }}</strong></div>
      </div>

      <div class="card" style="margin-top: 10px;">
        <h3>Task Ingest</h3>
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
          <button (click)="ingest()" [disabled]="!newTask.description.trim()">Create in Central Queue</button>
        </div>
      </div>

      <div class="card" style="margin-top: 10px; border-left: 4px solid #8b5cf6;">
        <div class="row" style="justify-content: space-between; align-items: center;">
          <h3 style="margin: 0;">Auto-Planner Activity</h3>
          <div class="row" style="gap: 10px;">
            <button class="secondary" style="padding: 4px 10px; font-size: 12px;" [routerLink]="['/auto-planner']">Konfigurieren</button>
            <button class="secondary" style="padding: 4px 10px; font-size: 12px;" (click)="reloadAutoPlanner()">Refresh</button>
          </div>
        </div>
        @if (autoPlannerLoading) {
          <div class="muted" style="margin-top: 10px;">Lade Auto-Planner Status...</div>
        }
        @if (autoPlannerStatus) {
          <div class="grid cols-4" style="margin-top: 10px;">
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
            <div style="margin-top: 8px; padding: 8px; background: #fef2f2; border-radius: 4px; border: 1px solid #fca5a5;">
              <strong class="danger">Fehler: {{ autoPlannerStatus.stats.errors }}</strong>
            </div>
          }
        }
        @if (autoPlannerRecentGoals.length) {
          <div style="margin-top: 12px;">
            <h4 style="margin: 0 0 8px 0;">Kürzliche Goals</h4>
            <div style="max-height: 200px; overflow: auto; border: 1px solid #e5e7eb; border-radius: 6px;">
              @for (goal of autoPlannerRecentGoals; track goal.id) {
                <div style="padding: 8px 10px; border-bottom: 1px solid #f3f4f6;">
                  <div class="row" style="justify-content: space-between;">
                    <strong style="font-size: 13px;">{{ goal.goal?.slice(0, 60) }}{{ goal.goal?.length > 60 ? '...' : '' }}</strong>
                    <span class="muted" style="font-size: 11px;">{{ goal.tasks_count || 0 }} Tasks</span>
                  </div>
                  @if (goal.created_at) {
                    <div class="muted" style="font-size: 11px; margin-top: 4px;">
                      {{ goal.created_at * 1000 | date:'dd.MM. HH:mm' }}
                    </div>
                  }
                </div>
              }
            </div>
          </div>
        } @else if (autoPlannerStatus) {
          <div class="muted" style="margin-top: 10px; font-size: 12px;">Noch keine Goals verarbeitet.</div>
        }
      </div>

      <div class="card" style="margin-top: 10px;">
        <h3>Recent Tasks</h3>
        <table style="width: 100%;">
          <thead><tr><th>ID</th><th>Status</th><th>Agent</th><th>Action</th></tr></thead>
          <tbody>
            @for (t of rm?.recent_tasks || []; track t.id) {
              <tr>
                <td style="font-family: monospace;">{{ t.id }}</td>
                <td>{{ t.status }}</td>
                <td>{{ t.assigned_agent_url || '-' }}</td>
                <td>
                  <button class="button-outline" (click)="claim(t.id)">Claim</button>
                  <button class="button-outline" (click)="complete(t.id)">Complete</button>
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    }
  `,
})
export class OperationsConsoleComponent implements OnInit {
  private dir = inject(AgentDirectoryService);
  private api = inject(HubApiService);
  private ns = inject(NotificationService);
  hub = this.dir.list().find((a) => a.role === 'hub');
  rm: any = null;
  newTask = { title: '', description: '', priority: 'medium', source: 'ui' };
  autoPlannerStatus: any = null;
  autoPlannerLoading = false;
  autoPlannerRecentGoals: any[] = [];

  ngOnInit() {
    this.reload();
  }

  reload() {
    if (!this.hub) return;
    this.api.getTaskOrchestrationReadModel(this.hub.url).subscribe({
      next: (r) => (this.rm = r),
      error: () => this.ns.error('Read-model konnte nicht geladen werden'),
    });
    this.reloadAutoPlanner();
  }

  reloadAutoPlanner() {
    if (!this.hub) return;
    this.autoPlannerLoading = true;
    this.api.getAutoPlannerStatus(this.hub.url).subscribe({
      next: (status) => {
        this.autoPlannerStatus = status;
        this.autoPlannerLoading = false;
      },
      error: () => {
        this.autoPlannerLoading = false;
        this.autoPlannerStatus = null;
      }
    });
  }

  ingest() {
    if (!this.hub) return;
    this.api.ingestOrchestrationTask(this.hub.url, { ...this.newTask, created_by: 'ui-operator' }).subscribe({
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
    this.api.claimOrchestrationTask(this.hub.url, { task_id: taskId, agent_url: this.hub.url, lease_seconds: 120 }).subscribe({
      next: () => this.reload(),
      error: () => this.ns.error('Claim fehlgeschlagen'),
    });
  }

  complete(taskId: string) {
    if (!this.hub) return;
    this.api.completeOrchestrationTask(this.hub.url, { task_id: taskId, actor: 'ui-operator', gate_results: { passed: true } }).subscribe({
      next: () => this.reload(),
      error: () => this.ns.error('Complete fehlgeschlagen'),
    });
  }
}
