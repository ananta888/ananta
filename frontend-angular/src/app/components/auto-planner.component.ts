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
  template: `
    <div class="card">
      <h3>Auto-Planner</h3>
      <p class="muted" style="margin-top: 4px;">Goal-basierte Task-Generierung mit LLM-Unterstützung.</p>

      @if (status) {
        <div class="grid cols-4" style="margin-top: 12px;">
          <div class="card" style="background: #f8fafc;">
            <div class="muted">Status</div>
            <strong [class.success]="status.enabled" [class.danger]="!status.enabled">{{ status.enabled ? 'Aktiv' : 'Inaktiv' }}</strong>
          </div>
          <div class="card" style="background: #f8fafc;">
            <div class="muted">Goals verarbeitet</div>
            <strong>{{ status.stats?.goals_processed || 0 }}</strong>
          </div>
          <div class="card" style="background: #f8fafc;">
            <div class="muted">Tasks erstellt</div>
            <strong>{{ status.stats?.tasks_created || 0 }}</strong>
          </div>
          <div class="card" style="background: #f8fafc;">
            <div class="muted">Followups</div>
            <strong>{{ status.stats?.followups_created || 0 }}</strong>
          </div>
        </div>
      }

      <div class="card" style="margin-top: 14px; background: #fafafa;">
        <h4 style="margin-top: 0;">Konfiguration</h4>
        <div class="grid cols-3" style="margin-top: 10px;">
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
            Default Priorität
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
        <button style="margin-top: 10px;" (click)="saveConfig()" [disabled]="saving">Speichern</button>
      </div>

      <div class="card" style="margin-top: 14px;">
        <h4 style="margin-top: 0;">Neues Goal planen</h4>
        <div style="margin-top: 10px;">
          <label>
            Goal-Beschreibung
            <textarea [(ngModel)]="goalForm.goal" rows="3" placeholder="z.B. Implementiere User-Login mit JWT-Authentifizierung" style="width: 100%;"></textarea>
          </label>
        </div>
        <div class="grid cols-2" style="margin-top: 10px;">
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
        <div class="row" style="gap: 8px; margin-top: 12px;">
          <button (click)="planGoal()" [disabled]="planning || !goalForm.goal?.trim()">
            {{ planning ? 'Plane...' : 'Goal planen' }}
          </button>
          <label style="display: flex; align-items: center; gap: 8px;">
            <input type="checkbox" [(ngModel)]="goalForm.create_tasks" />
            Tasks sofort erstellen
          </label>
        </div>

        @if (planningResult) {
          <div style="margin-top: 14px; padding: 12px; background: #f0fdf4; border-radius: 6px;">
            <h4 style="margin-top: 0; color: #166534;">Planung abgeschlossen</h4>
            <div class="muted" style="font-size: 12px;">
              {{ planningResult.created_task_ids?.length || 0 }} Tasks erstellt
            </div>
            @if (planningResult.subtasks?.length) {
              <div style="margin-top: 8px;">
                @for (st of planningResult.subtasks; track $index; let i = $index) {
                  <div style="padding: 6px 0; border-bottom: 1px solid #e5e7eb;">
                    <strong>{{ i + 1 }}. {{ st.title || st.description }}</strong>
                    <span class="muted" style="margin-left: 8px;">{{ st.priority }}</span>
                  </div>
                }
              </div>
            }
          </div>
        }
      </div>

      @if (recentTasks.length) {
        <div class="card" style="margin-top: 14px;">
          <h4 style="margin-top: 0;">Kürzlich erstellte Auto-Planner Tasks</h4>
          <div style="max-height: 200px; overflow: auto;">
            @for (t of recentTasks; track t.id) {
              <div style="padding: 8px 0; border-bottom: 1px solid #f0f0f0;">
                <div class="row" style="justify-content: space-between;">
                  <span>{{ t.title || t.description?.substring(0, 50) }}</span>
                  <span class="badge" [class.success]="t.status === 'completed'" [class.danger]="t.status === 'failed'">{{ t.status }}</span>
                </div>
                <div class="muted" style="font-size: 11px;">{{ t.id }}</div>
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
        if (s) {
          this.config = { ...this.config, ...s };
        }
      },
      error: () => this.ns.error('Auto-Planner Status konnte nicht geladen werden')
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
      error: () => {
        this.saving = false;
        this.ns.error('Konfiguration konnte nicht gespeichert werden');
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
      error: () => {
        this.planning = false;
        this.ns.error('Goal-Planung fehlgeschlagen');
      }
    });
  }
}
