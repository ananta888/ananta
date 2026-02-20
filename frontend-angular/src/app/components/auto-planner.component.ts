import { Component, inject, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-auto-planner',
  imports: [CommonModule, FormsModule],
  styles: [`
    .ap-subtitle { margin-top: 4px; }
    .ap-grid-top { margin-top: 12px; }
    .ap-muted-card { background: #f8fafc; }
    .ap-section { margin-top: 14px; background: #fafafa; }
    .ap-section-title { margin-top: 0; }
    .ap-grid { margin-top: 10px; }
    .ap-save-btn { margin-top: 10px; }
    .ap-goal-area { margin-top: 10px; }
    .ap-goal-input { width: 100%; }
    .ap-actions { gap: 8px; margin-top: 12px; }
    .ap-actions-label { display: flex; align-items: center; gap: 8px; }
    .ap-result { margin-top: 14px; padding: 12px; background: #f0fdf4; border-radius: 6px; }
    .ap-result-title { margin-top: 0; color: #166534; }
    .ap-result-meta { font-size: 12px; }
    .ap-subtask-list { margin-top: 8px; }
    .ap-subtask-item { padding: 6px 0; border-bottom: 1px solid #e5e7eb; }
    .ap-subtask-prio { margin-left: 8px; }
    .ap-recent-list { max-height: 200px; overflow: auto; }
    .ap-recent-item { padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
    .ap-recent-row { justify-content: space-between; }
    .ap-recent-id { font-size: 11px; }
  `],
  template: `
    <div class="card">
      <h3>Auto-Planner</h3>
      <p class="muted ap-subtitle">Goal-basierte Task-Generierung mit LLM-Unterstuetzung.</p>

      @if (status) {
        <div class="grid cols-4 ap-grid-top">
          <div class="card ap-muted-card">
            <div class="muted">Status</div>
            <strong [class.success]="status.enabled" [class.danger]="!status.enabled">{{ status.enabled ? 'Aktiv' : 'Inaktiv' }}</strong>
          </div>
          <div class="card ap-muted-card">
            <div class="muted">Goals verarbeitet</div>
            <strong>{{ status.stats?.goals_processed || 0 }}</strong>
          </div>
          <div class="card ap-muted-card">
            <div class="muted">Tasks erstellt</div>
            <strong>{{ status.stats?.tasks_created || 0 }}</strong>
          </div>
          <div class="card ap-muted-card">
            <div class="muted">Followups</div>
            <strong>{{ status.stats?.followups_created || 0 }}</strong>
          </div>
        </div>
      }

      <div class="card ap-section">
        <h4 class="ap-section-title">Konfiguration</h4>
        <div class="grid cols-3 ap-grid">
          <label>
            <input type="checkbox" [(ngModel)]="config.enabled" />
            Auto-Planner aktivieren
          </label>
          <label>
            <input type="checkbox" [(ngModel)]="config.auto_followup_enabled" />
            Automatische Followups
          </label>
          <label>
            <input type="checkbox" [(ngModel)]="config.auto_start_autopilot" />
            Autopilot auto-starten
          </label>
          <label>
            Max Subtasks
            <input type="number" min="1" max="20" [(ngModel)]="config.max_subtasks_per_goal" />
          </label>
          <label>
            Default Prioritaet
            <select [(ngModel)]="config.default_priority">
              <option value="Low">Low</option>
              <option value="Medium">Medium</option>
              <option value="High">High</option>
            </select>
          </label>
          <label>
            LLM Timeout (s)
            <input type="number" min="5" max="120" [(ngModel)]="config.llm_timeout" />
          </label>
        </div>
        <button class="ap-save-btn" (click)="saveConfig()" [disabled]="saving" title="Speichert Auto-Planner-Konfiguration">Speichern</button>
      </div>

      <div class="card ap-section">
        <h4 class="ap-section-title">Neues Goal planen</h4>
        <div class="ap-goal-area">
          <label>
            Goal-Beschreibung
            <textarea class="ap-goal-input" [(ngModel)]="goalForm.goal" rows="3" placeholder="z.B. Implementiere User-Login mit JWT-Authentifizierung"></textarea>
          </label>
        </div>
        <div class="grid cols-2 ap-grid">
          <label>
            Kontext (optional)
            <input [(ngModel)]="goalForm.context" placeholder="z.B. Verwende Flask und PostgreSQL" />
          </label>
          <label>
            Team
            <select [(ngModel)]="goalForm.team_id">
              <option value="">Kein Team</option>
              @for (t of teams; track t.id) {
                <option [value]="t.id">{{ t.name }}</option>
              }
            </select>
          </label>
        </div>
        <div class="row ap-actions">
          <button (click)="planGoal()" [disabled]="planning || !goalForm.goal?.trim()" title="Erstellt aus dem Goal konkrete Subtasks">
            {{ planning ? 'Plane...' : 'Goal planen' }}
          </button>
          <label class="ap-actions-label">
            <input type="checkbox" [(ngModel)]="goalForm.create_tasks" />
            Tasks sofort erstellen
          </label>
        </div>

        @if (planningResult) {
          <div class="ap-result">
            <h4 class="ap-result-title">Planung abgeschlossen</h4>
            <div class="muted ap-result-meta">
              {{ planningResult.created_task_ids?.length || 0 }} Tasks erstellt
            </div>
            @if (planningResult.subtasks?.length) {
              <div class="ap-subtask-list">
                @for (st of planningResult.subtasks; track $index; let i = $index) {
                  <div class="ap-subtask-item">
                    <strong>{{ i + 1 }}. {{ st.title || st.description }}</strong>
                    <span class="muted ap-subtask-prio">{{ st.priority }}</span>
                  </div>
                }
              </div>
            }
          </div>
        }
      </div>

      @if (recentTasks.length) {
        <div class="card ap-section">
          <h4 class="ap-section-title">Kuerzlich erstellte Auto-Planner Tasks</h4>
          <div class="ap-recent-list">
            @for (t of recentTasks; track t.id) {
              <div class="ap-recent-item">
                <div class="row ap-recent-row">
                  <span>{{ t.title || t.description?.substring(0, 50) }}</span>
                  <span class="badge" [class.success]="t.status === 'completed'" [class.danger]="t.status === 'failed'">{{ t.status }}</span>
                </div>
                <div class="muted ap-recent-id">{{ t.id }}</div>
              </div>
            }
          </div>
        </div>
      }
    </div>
  `
})
export class AutoPlannerComponent implements OnInit {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);

  hub = this.dir.list().find(a => a.role === 'hub');
  status: any = null;
  config: any = {
    enabled: false,
    auto_followup_enabled: true,
    auto_start_autopilot: true,
    max_subtasks_per_goal: 10,
    default_priority: 'Medium',
    llm_timeout: 30
  };
  goalForm: any = {
    goal: '',
    context: '',
    team_id: '',
    create_tasks: true
  };
  teams: any[] = [];
  planning = false;
  saving = false;
  planningResult: any = null;
  recentTasks: any[] = [];

  ngOnInit() {
    this.refresh();
  }

  refresh() {
    if (!this.hub) {
      this.hub = this.dir.list().find(a => a.role === 'hub');
    }
    if (!this.hub) return;

    this.hubApi.getAutoPlannerStatus(this.hub.url).subscribe({
      next: (s) => {
        this.status = s;
        if (s) this.config = { ...this.config, ...s };
      },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Auto-Planner Status konnte nicht geladen werden'))
    });

    this.hubApi.listTeams(this.hub.url).subscribe({
      next: (teams) => this.teams = Array.isArray(teams) ? teams : [],
      error: () => {}
    });
  }

  saveConfig() {
    if (!this.hub) return;
    this.saving = true;
    this.hubApi.configureAutoPlanner(this.hub.url, this.config).subscribe({
      next: (s) => {
        this.status = s;
        this.saving = false;
        this.ns.success('Auto-Planner Konfiguration gespeichert');
      },
      error: (e) => {
        this.saving = false;
        this.ns.error(this.ns.fromApiError(e, 'Konfiguration konnte nicht gespeichert werden'));
      }
    });
  }

  planGoal() {
    if (!this.hub || !this.goalForm.goal?.trim()) return;
    this.planning = true;
    this.planningResult = null;

    this.hubApi.planGoal(this.hub.url, {
      goal: this.goalForm.goal,
      context: this.goalForm.context || undefined,
      team_id: this.goalForm.team_id || undefined,
      create_tasks: this.goalForm.create_tasks
    }).subscribe({
      next: (result) => {
        this.planning = false;
        this.planningResult = result;
        if (result.created_task_ids?.length) {
          this.ns.success(`${result.created_task_ids.length} Tasks erstellt`);
          this.goalForm.goal = '';
          this.goalForm.context = '';
        }
      },
      error: (e) => {
        this.planning = false;
        this.ns.error(this.ns.fromApiError(e, 'Goal-Planung fehlgeschlagen'));
      }
    });
  }
}
