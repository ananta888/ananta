import { ChangeDetectorRef, Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { ControlPlaneFacade } from '../features/control-plane/control-plane.facade';
import { UserAuthService } from '../services/user-auth.service';
import { DecisionExplanationComponent, NextStepAction, NextStepsComponent, SafetyNoticeComponent } from '../shared/ui/display';

@Component({
  standalone: true,
  selector: 'app-auto-planner',
  imports: [CommonModule, FormsModule, DecisionExplanationComponent, NextStepsComponent, SafetyNoticeComponent],
  styles: [`
    .ap-subtitle { margin-top: 4px; }
    .ap-grid-top { margin-top: 12px; }
    .ap-muted-card { background: #f7faf7; border: 1px solid #d9e6da; }
    .ap-section { margin-top: 14px; background: #fffdf8; border: 1px solid #eadfca; }
    .ap-section-title { margin-top: 0; }
    .ap-grid { margin-top: 10px; }
    .ap-save-btn { margin-top: 10px; }
    .ap-goal-area { margin-top: 10px; }
    .ap-goal-input { width: 100%; }
    .ap-actions { gap: 8px; margin-top: 12px; flex-wrap: wrap; }
    .ap-actions-label { display: flex; align-items: center; gap: 8px; }
    .ap-result { margin-top: 14px; padding: 12px; background: #eef8f0; border-radius: 8px; }
    .ap-result-title { margin-top: 0; color: #215732; }
    .ap-result-meta { font-size: 12px; }
    .ap-subtask-list { margin-top: 8px; }
    .ap-subtask-item { padding: 8px 0; border-bottom: 1px solid #e5e7eb; }
    .ap-subtask-prio { margin-left: 8px; }
    .ap-recent-list { max-height: 260px; overflow: auto; }
    .ap-recent-item { padding: 10px 0; border-bottom: 1px solid #eee4d6; cursor: pointer; }
    .ap-recent-item.active { background: rgba(193, 153, 82, 0.08); }
    .ap-recent-row { justify-content: space-between; gap: 12px; }
    .ap-recent-id { font-size: 11px; }
    .ap-mode-toggle { margin-top: 8px; }
    .ap-detail-grid { display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 14px; }
    .ap-stack { display: grid; gap: 14px; }
    .ap-kicker { text-transform: uppercase; letter-spacing: 0.08em; font-size: 11px; color: #8c5d1a; }
    .ap-mini-list { display: grid; gap: 8px; margin-top: 10px; }
    .ap-mini-card { border: 1px solid #ece5d9; border-radius: 8px; padding: 10px; background: #fff; }
    .ap-detail-meta { font-size: 12px; color: #6b7280; }
    .ap-node-actions { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
    .ap-inline-edit { margin-top: 8px; display: grid; gap: 8px; }
    .ap-artifact { background: #fcfaf4; border-left: 4px solid #b78735; }
    .ap-goal-list-title { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
    @media (max-width: 960px) {
      .ap-detail-grid { grid-template-columns: 1fr; }
    }
  `],
  template: `
    <div class="card">
      <h3>Goal Workspace</h3>
      <p class="muted ap-subtitle">Goal-first Einstieg mit sicheren Defaults. Advanced- und Governance-Drilldowns sind additive Optionen.</p>

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
        <h4 class="ap-section-title" data-testid="auto-planner-config-title">Konfiguration</h4>
        <div class="grid cols-3 ap-grid">
          <label>
            <input type="checkbox" data-testid="auto-planner-config-enabled" [(ngModel)]="config.enabled" />
            Auto-Planner aktivieren
          </label>
          <label>
            <input type="checkbox" data-testid="auto-planner-config-followups" [(ngModel)]="config.auto_followup_enabled" />
            Automatische Followups
          </label>
          <label>
            <input type="checkbox" data-testid="auto-planner-config-autostart" [(ngModel)]="config.auto_start_autopilot" />
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
        <button class="ap-save-btn" data-testid="auto-planner-config-save" (click)="saveConfig()" [disabled]="saving">Speichern</button>
      </div>

      <div class="ap-detail-grid">
        <div class="ap-stack">
          <div class="card ap-section">
            <div class="ap-goal-list-title">
              <div>
                <h4 class="ap-section-title" data-testid="auto-planner-goal-title">Goal erfassen</h4>
                <div class="muted">Simple zuerst, erweiterte Routing-, Constraint- und Governance-Felder nur bei Bedarf.</div>
                @if (!isAdmin) {
                  <div class="muted font-sm mt-sm">Safe Defaults aktiv: Routing-/Security-Policies bleiben im Standardmodus.</div>
                }
              </div>
              <button class="secondary ap-mode-toggle" type="button" data-testid="goal-mode-toggle" (click)="advancedMode = !advancedMode">
                {{ advancedMode ? 'Advanced verbergen' : 'Advanced zeigen' }}
              </button>
            </div>

            <div class="ap-goal-area">
              <label>
                Goal-Beschreibung
                <textarea class="ap-goal-input" data-testid="auto-planner-goal-input" [(ngModel)]="goalForm.goal" rows="3" placeholder="z.B. Fuehre ein Goal-first Release mit nachvollziehbarer Planung ein"></textarea>
              </label>
            </div>

            <div class="grid cols-2 ap-grid">
              <label>
                Kontext (optional)
                <input [(ngModel)]="goalForm.context" placeholder="Rahmen, Repo-Hinweise oder technische Einschraenkungen" />
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

            @if (advancedMode) {
              <div class="grid cols-2 ap-grid" data-testid="goal-advanced-fields">
                <label>
                  Constraints
                  <textarea [(ngModel)]="goalForm.constraintsText" rows="3" placeholder="Eine Zeile pro Constraint"></textarea>
                </label>
                <label>
                  Acceptance Criteria
                  <textarea [(ngModel)]="goalForm.acceptanceCriteriaText" rows="3" placeholder="Eine Zeile pro Kriterium"></textarea>
                </label>
                @if (isAdmin) {
                  <label>
                    Sicherheitsniveau
                    <select [(ngModel)]="goalForm.securityLevel">
                      <option value="safe_defaults">safe_defaults</option>
                      <option value="strict">strict</option>
                    </select>
                  </label>
                  <label>
                    Routing-Praeferenz
                    <input [(ngModel)]="goalForm.routingPreference" placeholder="z.B. active_team_or_hub_default" />
                  </label>
                } @else {
                  <div class="card ap-muted-card">
                    <div class="ap-kicker">Admin Drilldown</div>
                    <div class="ap-detail-meta">Security-Level und Routing-Praefenzen sind nur fuer Admins konfigurierbar.</div>
                  </div>
                }
              </div>
            }

            <div class="row ap-actions">
              <button data-testid="auto-planner-goal-plan" (click)="planGoal()" [disabled]="planning || !goalForm.goal?.trim()">
                {{ planning ? 'Plane...' : 'Goal planen' }}
              </button>
              <label class="ap-actions-label">
                <input type="checkbox" data-testid="auto-planner-goal-create-tasks" [(ngModel)]="goalForm.create_tasks" />
                Tasks sofort erstellen
              </label>
            </div>

            @if (planningResult) {
              <div class="ap-result" data-testid="goal-submit-result">
                <h4 class="ap-result-title">Goal angelegt</h4>
                <div class="muted ap-result-meta">
                  {{ planningResult.goal?.id }} · {{ planningResult.created_task_ids?.length || 0 }} Tasks · Plan {{ planningResult.plan_id || '-' }}
                </div>
                <div class="grid cols-2 mt-sm">
                  <app-decision-explanation kind="routing" title="Warum Routing sichtbar bleibt"></app-decision-explanation>
                  <app-decision-explanation kind="verification"></app-decision-explanation>
                </div>
                <div class="ap-subtask-list" *ngIf="planningResult.subtasks?.length">
                  @for (st of planningResult.subtasks; track $index; let i = $index) {
                    <div class="ap-subtask-item">
                      <strong>{{ i + 1 }}. {{ st.title || st.description }}</strong>
                      <span class="muted ap-subtask-prio">{{ st.priority }}</span>
                    </div>
                  }
                </div>
                <app-next-steps class="block mt-sm" [steps]="autoPlannerNextSteps()"></app-next-steps>
              </div>
            }
          </div>

          @if (selectedGoalDetail) {
            <div class="card ap-section" data-testid="goal-detail-panel">
              <div class="ap-kicker">Goal Detail</div>
              <h4 class="ap-section-title">{{ selectedGoalDetail.goal?.summary || selectedGoalDetail.goal?.goal }}</h4>
              <div class="ap-detail-meta">
                {{ selectedGoalDetail.goal?.status }} · Trace {{ selectedGoalDetail.trace?.trace_id }} · {{ selectedGoalDetail.tasks?.length || 0 }} Tasks
              </div>
              <app-safety-notice class="block mt-sm" title="Hinweis" message="Wenn ein Goal blockiert oder review-required ist: Timeline und Settings helfen, den Grund und naechste Schritte zu sehen."></app-safety-notice>
              <app-next-steps class="block mt-sm" [steps]="goalDetailNextSteps()"></app-next-steps>

              <div class="ap-mini-list">
                <div class="ap-mini-card ap-artifact" data-testid="goal-artifact-summary">
                  <div class="ap-kicker">Artifact-First Result</div>
                  <strong>{{ selectedGoalDetail.artifacts?.result_summary?.completed_tasks || 0 }} abgeschlossen, {{ selectedGoalDetail.artifacts?.result_summary?.failed_tasks || 0 }} fehlgeschlagen</strong>
                  <div class="ap-detail-meta">
                    {{ selectedGoalDetail.artifacts?.headline_artifact?.preview || 'Noch kein Ergebnisartefakt vorhanden.' }}
                  </div>
                </div>

                <div class="ap-mini-card" data-testid="goal-plan-panel">
                  <div class="ap-kicker">Plan</div>
                  @if (selectedGoalDetail.plan?.nodes?.length) {
                    @for (node of selectedGoalDetail.plan.nodes; track node.id) {
                      <div class="ap-subtask-item">
                        <strong>{{ node.title }}</strong>
                        <div class="ap-detail-meta">{{ node.status }} · {{ node.priority }} · {{ node.node_key }}</div>
                        <div class="ap-node-actions">
                          <button class="secondary btn-small" type="button" (click)="startNodeEdit(node)">Plan anpassen</button>
                        </div>
                        @if (editingNodeId === node.id) {
                          <div class="ap-inline-edit">
                            <input [(ngModel)]="editingNode.title" placeholder="Titel" />
                            <select [(ngModel)]="editingNode.priority">
                              <option value="Low">Low</option>
                              <option value="Medium">Medium</option>
                              <option value="High">High</option>
                            </select>
                            <button type="button" (click)="saveNodeEdit()">Speichern</button>
                          </div>
                        }
                      </div>
                    }
                  } @else {
                    <div class="muted">Kein persistierter Plan vorhanden.</div>
                  }
                </div>
              </div>
            </div>
          }
        </div>

        <div class="ap-stack">
          @if (isAdmin) {
            <div class="card ap-section">
              <div class="row space-between">
                <div>
                  <div class="ap-kicker">Admin Drilldown</div>
                  <div class="ap-detail-meta">Trace-, Governance- und Verification-Details werden nur auf Anforderung gezeigt.</div>
                </div>
                <button class="secondary btn-small" type="button" data-testid="goal-admin-drilldown-toggle" (click)="showAdminDrilldown = !showAdminDrilldown">
                  {{ showAdminDrilldown ? 'Drilldown ausblenden' : 'Drilldown zeigen' }}
                </button>
              </div>
            </div>
          }

          <div class="card ap-section">
            <h4 class="ap-section-title">Goals</h4>
            <div class="ap-recent-list" data-testid="goal-list">
              @for (goal of goals(); track goal.id) {
                <div class="ap-recent-item" [class.active]="goal.id === selectedGoalId" (click)="selectGoal(goal.id)">
                  <div class="row ap-recent-row">
                    <span>{{ goal.summary || goal.goal }}</span>
                    <span class="badge">{{ goal.status }}</span>
                  </div>
                  <div class="muted ap-recent-id">{{ goal.id }}</div>
                </div>
              }
            </div>
          </div>

          @if (isAdmin && showAdminDrilldown && selectedGoalDetail?.governance) {
            <div class="card ap-section" data-testid="goal-governance-panel">
              <h4 class="ap-section-title">Governance</h4>
              <div class="ap-mini-list">
                <div class="ap-mini-card">
                  <div class="ap-kicker">Policy</div>
                  <strong>{{ selectedGoalDetail.governance.policy?.approved || 0 }} freigegeben</strong>
                  <div class="ap-detail-meta">Total {{ selectedGoalDetail.governance.policy?.total || 0 }}, blocked {{ selectedGoalDetail.governance.policy?.blocked || 0 }}</div>
                </div>
                <div class="ap-mini-card">
                  <div class="ap-kicker">Verification</div>
                  <strong>{{ selectedGoalDetail.governance.verification?.passed || 0 }} bestanden</strong>
                  <div class="ap-detail-meta">Total {{ selectedGoalDetail.governance.verification?.total || 0 }}, escalated {{ selectedGoalDetail.governance.verification?.escalated || 0 }}</div>
                </div>
                <div class="ap-mini-card" *ngIf="selectedGoalDetail.governance.summary">
                  <div class="ap-kicker">Sichtbarkeit</div>
                  <div class="ap-detail-meta">
                    {{ selectedGoalDetail.governance.summary.detail_level || 'full' }} · visible={{ selectedGoalDetail.governance.summary.governance_visible }}
                  </div>
                </div>
              </div>
            </div>
          }

          @if (isAdmin && showAdminDrilldown && selectedGoalDetail?.tasks?.length) {
            <div class="card ap-section" data-testid="goal-trace-panel">
              <h4 class="ap-section-title">Tasks und Trace</h4>
              <div class="ap-mini-list">
                @for (task of selectedGoalDetail.tasks; track task.id) {
                  <div class="ap-mini-card">
                    <strong>{{ task.title || task.id }}</strong>
                    <div class="ap-detail-meta">{{ task.status }} · {{ task.trace_id }} · verification={{ task.verification_status?.status || '-' }}</div>
                  </div>
                }
              </div>
            </div>
          }
        </div>
      </div>
    </div>
  `
})
export class AutoPlannerComponent implements OnInit {
  private cdr = inject(ChangeDetectorRef);
  private controlPlane = inject(ControlPlaneFacade);
  private dir = inject(AgentDirectoryService);
  private ns = inject(NotificationService);
  private auth = inject(UserAuthService);

  hub = this.dir.list().find((a) => a.role === 'hub');
  status: any = null;
  config: any = {
    enabled: false,
    auto_followup_enabled: true,
    auto_start_autopilot: true,
    max_subtasks_per_goal: 10,
    default_priority: 'Medium',
    llm_timeout: 30,
  };
  goalForm: any = {
    goal: '',
    context: '',
    team_id: '',
    create_tasks: true,
    constraintsText: '',
    acceptanceCriteriaText: '',
    securityLevel: 'safe_defaults',
    routingPreference: 'active_team_or_hub_default',
  };
  teams: any[] = [];
  goals = signal<any[]>([]);
  planning = false;
  saving = false;
  planningResult: any = null;
  advancedMode = false;
  isAdmin = false;
  showAdminDrilldown = false;
  selectedGoalId = '';
  selectedGoalDetail: any = null;
  editingNodeId = '';
  editingNode: any = { title: '', priority: 'Medium' };

  ngOnInit() {
    const user = this.auth.decodeTokenPayload(this.auth.token);
    this.isAdmin = user?.role === 'admin';
    this.refresh();
  }

  refresh() {
    if (!this.hub) {
      this.hub = this.dir.list().find((a) => a.role === 'hub');
    }
    if (!this.hub) return;

    this.controlPlane.getAutopilotStatus(this.hub.url).subscribe({
      next: (s) => {
        this.status = s;
        if (s) this.config = { ...this.config, ...s };
        this.cdr.detectChanges();
      },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Auto-Planner Status konnte nicht geladen werden')),
    });

    this.controlPlane.listTeams(this.hub.url).subscribe({
      next: (teams) => {
        this.teams = Array.isArray(teams) ? teams : [];
        this.goalForm.team_id = this.resolvePreferredTeamId(this.goalForm.team_id);
        this.cdr.detectChanges();
      },
      error: () => {},
    });

    this.loadGoals();
  }

  loadGoals(selectFirst = true) {
    if (!this.hub) return;
    this.controlPlane.listGoals(this.hub.url).subscribe({
      next: (goals) => {
        const nextGoals = Array.isArray(goals) ? goals : [];
        this.goals.set(nextGoals);
        if (this.selectedGoalId) {
          this.selectGoal(this.selectedGoalId, false);
        } else if (selectFirst && nextGoals.length) {
          this.selectGoal(nextGoals[0].id, false);
        }
        this.cdr.detectChanges();
      },
      error: () => {
        this.goals.set([]);
        this.cdr.detectChanges();
      },
    });
  }

  saveConfig() {
    if (!this.hub) return;
    this.saving = true;
    this.controlPlane.configureAutoPlanner(this.hub.url, this.config).subscribe({
      next: (s) => {
        this.status = s;
        this.saving = false;
        this.cdr.detectChanges();
        this.ns.success('Auto-Planner Konfiguration gespeichert');
      },
      error: (e) => {
        this.saving = false;
        this.cdr.detectChanges();
        this.ns.error(this.ns.fromApiError(e, 'Konfiguration konnte nicht gespeichert werden'));
      },
    });
  }

  planGoal() {
    if (!this.hub || !this.goalForm.goal?.trim()) return;
    this.planning = true;
    this.planningResult = null;
    const llmTimeoutSeconds = Math.max(5, Number(this.config?.llm_timeout) || 30);
    const llmRetryAttempts = Math.max(1, Number(this.config?.llm_retry_attempts) || 2);
    const goalTimeoutMs = Math.max(120000, ((llmTimeoutSeconds * llmRetryAttempts) + 30) * 1000);
    const effectiveTeamId = String(this.resolvePreferredTeamId(this.goalForm.team_id)).trim();

    const body: any = {
      goal: this.goalForm.goal.trim(),
      context: this.goalForm.context || undefined,
      team_id: effectiveTeamId || undefined,
      create_tasks: this.goalForm.create_tasks,
    };

    if (this.advancedMode) {
      const constraints = this.toLines(this.goalForm.constraintsText);
      const acceptanceCriteria = this.toLines(this.goalForm.acceptanceCriteriaText);
      if (constraints.length) body.constraints = constraints;
      if (acceptanceCriteria.length) body.acceptance_criteria = acceptanceCriteria;
      if (this.isAdmin) {
        body.workflow = {
          routing: { mode: this.goalForm.routingPreference || 'active_team_or_hub_default' },
          policy: { security_level: this.goalForm.securityLevel || 'safe_defaults' },
        };
      }
    }

    this.controlPlane.createGoal(this.hub.url, body, undefined, goalTimeoutMs).subscribe({
      next: (result) => {
        this.planning = false;
        this.planningResult = result;
        this.selectedGoalId = result.goal?.id || '';
        this.resetGoalForm();
        this.loadGoals(false);
        if (this.selectedGoalId) this.selectGoal(this.selectedGoalId, true);
        this.cdr.detectChanges();
        this.ns.success('Goal angelegt');
      },
      error: (e) => {
        this.planning = false;
        this.cdr.detectChanges();
        this.ns.error(this.ns.fromApiError(e, 'Goal-Planung fehlgeschlagen'));
      },
    });
  }

  autoPlannerNextSteps(): NextStepAction[] {
    const goalId = String(this.planningResult?.goal?.id || this.selectedGoalId || '').trim();
    return [
      { id: 'open-goal', label: 'Goal Detail oeffnen', description: 'Plan, Governance und Artefakte ansehen.', routerLink: goalId ? ['/goal', goalId] : ['/dashboard'] },
      { id: 'open-board', label: 'Board oeffnen', description: 'Tasks verfolgen und blockierte Punkte klaeren.', routerLink: ['/board'] },
      { id: 'open-dashboard', label: 'Dashboard oeffnen', description: 'Timeline/Guardrails und Governance-Summary ansehen.', routerLink: ['/dashboard'] },
      { id: 'open-settings', label: 'Settings oeffnen', description: 'Profiles & Governance sicht-/waehlbar machen.', routerLink: ['/settings'] },
    ];
  }

  goalDetailNextSteps(): NextStepAction[] {
    const goalId = String(this.selectedGoalId || '').trim();
    return [
      { id: 'open-goal', label: 'Goal Detail oeffnen', description: 'Zur Abschluss- und Ergebnisansicht wechseln.', routerLink: goalId ? ['/goal', goalId] : ['/dashboard'] },
      { id: 'open-board', label: 'Board oeffnen', description: 'Statuses, Blockierungen und Review-Pflichten sehen.', routerLink: ['/board'] },
      { id: 'open-settings', label: 'Policies/Profiles pruefen', description: 'Governance Mode und Runtime Profile abgleichen.', routerLink: ['/settings'] },
    ];
  }

  selectGoal(goalId: string, announce = false) {
    if (!this.hub || !goalId) return;
    this.selectedGoalId = goalId;
    this.editingNodeId = '';
    this.controlPlane.getGoalDetail(this.hub.url, goalId).subscribe({
      next: (detail) => {
        this.selectedGoalDetail = detail;
        this.cdr.detectChanges();
        if (announce) this.ns.success('Goal-Detail geladen');
      },
      error: (e) => {
        this.selectedGoalDetail = null;
        this.cdr.detectChanges();
        this.ns.error(this.ns.fromApiError(e, 'Goal-Detail konnte nicht geladen werden'));
      },
    });
  }

  startNodeEdit(node: any) {
    this.editingNodeId = node.id;
    this.editingNode = { title: node.title, priority: node.priority };
  }

  saveNodeEdit() {
    if (!this.hub || !this.selectedGoalId || !this.editingNodeId) return;
    this.controlPlane.patchGoalPlanNode(this.hub.url, this.selectedGoalId, this.editingNodeId, this.editingNode).subscribe({
      next: () => {
        this.editingNodeId = '';
        this.selectGoal(this.selectedGoalId);
        this.cdr.detectChanges();
        this.ns.success('Plan-Knoten aktualisiert');
      },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Plan-Knoten konnte nicht aktualisiert werden')),
    });
  }

  private toLines(value: string): string[] {
    return String(value || '')
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean);
  }

  private resetGoalForm() {
    this.goalForm.goal = '';
    this.goalForm.context = '';
    this.goalForm.constraintsText = '';
    this.goalForm.acceptanceCriteriaText = '';
  }

  resolvePreferredTeamId(currentTeamId: string): string {
    const selected = String(currentTeamId || '').trim();
    if (selected && this.teams.some((team) => String(team?.id || '') === selected)) {
      return selected;
    }
    const workerUrls = new Set(
      this.dir
        .list()
        .filter((agent) => agent.role === 'worker')
        .map((agent) => String(agent.url || '').trim())
        .filter(Boolean),
    );
    const teams = Array.isArray(this.teams) ? this.teams : [];
    const hasKnownWorkerMember = (team: any): boolean =>
      Array.isArray(team?.members)
      && team.members.some((member: any) => workerUrls.has(String(member?.agent_url || '').trim()));
    const preferred =
      teams.find((team) => team?.is_active && hasKnownWorkerMember(team))
      || teams.find((team) => hasKnownWorkerMember(team))
      || teams.find((team) => team?.is_active)
      || teams[0];
    return String(preferred?.id || '');
  }
}
