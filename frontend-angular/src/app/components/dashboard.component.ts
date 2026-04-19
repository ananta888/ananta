import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { ToastService } from '../services/toast.service';
import { UiAsyncState } from '../models/ui.models';
import {
  AgentEntry,
  ArtifactFlowStatus,
  AutopilotSecurityLevel,
  AutopilotStatus,
  BenchmarkItem,
  BenchmarkRecommendation,
  BenchmarkTaskKind,
  ContextPolicyStatus,
  ContractsStatus,
  DashboardStatsBlock,
  GoalListEntry,
  HubCopilotStatus,
  LlmEffectiveRuntime,
  LlmModelReference,
  ResearchBackendProvider,
  ResearchBackendStatus,
  RoleEntry,
  RuntimeTelemetry,
  SystemHealth,
  TeamEntry,
  TimelineEvent,
} from '../models/dashboard.models';
import { KeyValueItem, NextStepAction } from '../shared/ui/display';
import { StatusTone } from '../shared/ui/state';
import { OnboardingChecklistComponent } from './onboarding-checklist.component';
import { ControlPlaneFacade } from '../features/control-plane/control-plane.facade';
import { TaskManagementFacade } from '../features/tasks/task-management.facade';
import { DashboardAgentStatusPanelComponent } from './dashboard-agent-status-panel.component';
import { DashboardAutopilotPanelComponent } from './dashboard-autopilot-panel.component';
import { DashboardTimelinePanelComponent } from './dashboard-timeline-panel.component';
import { DashboardBenchmarkPanelComponent } from './dashboard-benchmark-panel.component';
import { DashboardDemoPreviewComponent, DemoPreviewExample } from './dashboard-demo-preview.component';
import { DashboardFacade } from './dashboard.facade';
import { DashboardGoalReportingFacade } from './dashboard-goal-reporting.facade';
import { DashboardGoalGovernanceSummaryCardComponent } from './dashboard-goal-governance-summary-card.component';
import { DashboardPersonalWorkspaceComponent } from './dashboard-personal-workspace.component';
import { DashboardRefreshRuntimeService } from '../services/dashboard-refresh-runtime.service';
import { DashboardWorkspaceViewModelService } from './dashboard-workspace-view-model.service';
import { EmptyStateComponent, ErrorStateComponent, LoadingStateComponent } from '../shared/ui/state';
import { ExplanationNoticeComponent, KeyValueGridComponent, NextStepsComponent, SafetyNoticeComponent, SystemStatusSummaryComponent, SystemStatusTeamMember } from '../shared/ui/display';
import { ActionCardComponent, PageIntroComponent, SectionCardComponent } from '../shared/ui/layout';
import { FormFieldComponent, ModeCardOption, ModeCardPickerComponent, PresetOption, PresetPickerComponent, WizardShellComponent } from '../shared/ui/forms';

@Component({
  standalone: true,
  selector: 'app-dashboard',
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    OnboardingChecklistComponent,
    DashboardAgentStatusPanelComponent,
    DashboardAutopilotPanelComponent,
    DashboardTimelinePanelComponent,
    DashboardBenchmarkPanelComponent,
    DashboardDemoPreviewComponent,
    DashboardGoalGovernanceSummaryCardComponent,
    DashboardPersonalWorkspaceComponent,
    EmptyStateComponent,
    ErrorStateComponent,
    LoadingStateComponent,
    KeyValueGridComponent,
    ExplanationNoticeComponent,
    NextStepsComponent,
    SafetyNoticeComponent,
    SystemStatusSummaryComponent,
    SectionCardComponent,
    PageIntroComponent,
    ActionCardComponent,
    FormFieldComponent,
    ModeCardPickerComponent,
    PresetPickerComponent,
    WizardShellComponent,
  ],
  template: `
    <app-page-intro
      title="Ananta starten"
      subtitle="Beschreibe ein Ziel, pruefe ein Beispiel oder gehe direkt zu Aufgaben und Ergebnissen."
    >
      <div intro-actions class="row gap-sm">
        <button class="primary" (click)="focusQuickGoal()">Ziel eingeben</button>
        <button class="secondary" (click)="loadDemoPreview()">Demo ansehen</button>
      </div>
    </app-page-intro>

    @if (showFirstStartWizard) {
      <section class="card first-start mb-md" aria-label="Erststart">
        <div class="row space-between">
          <div>
            <h3 class="no-margin">Wie moechtest du starten?</h3>
            <p class="muted mt-sm no-margin">Waehle einen einfachen Einstieg. Du kannst spaeter jederzeit in die tieferen Ansichten wechseln.</p>
          </div>
          <button class="secondary btn-small" (click)="completeFirstStartWizard()">Ausblenden</button>
        </div>
        <app-mode-card-picker
          class="block mt-sm"
          [options]="firstStartOptions"
          [columns]="3"
          ariaLabel="Erststart-Auswahl"
          (selectOption)="chooseFirstStart($event.id)"
        ></app-mode-card-picker>
      </section>
    }

    @if (viewState.loading) {
      <app-loading-state label="Dashboard wird geladen" [count]="1" [lineCount]="1" lineClass="skeleton block"></app-loading-state>
    }
    @if (viewState.error) {
      <app-error-state
        title="Dashboard konnte nicht geladen werden"
        [message]="viewState.error"
        retryLabel="Erneut versuchen"
        (retry)="refresh()"
      ></app-error-state>
    }
    @if (!viewState.loading && viewState.empty) {
      <app-empty-state
        title="Noch keine Arbeit sichtbar"
        description="Starte mit einem Ziel oder oeffne die Demo-Beispiele, um typische Ablaeufe kennenzulernen."
        primaryLabel="Ziel eingeben"
        secondaryLabel="Demo ansehen"
        (primary)="focusQuickGoal()"
        (secondary)="loadDemoPreview()"
      ></app-empty-state>
    }

    @if (hub) {
      <app-dashboard-personal-workspace
        [activeGoalCount]="activeGoalCount()"
        [nextTaskCount]="nextTaskCount()"
        [starterDone]="starterProgress().done"
        [starterTotal]="starterProgress().total"
        [starterLabel]="starterProgress().label"
        [recentGoals]="recentGoals()"
        [presets]="goalPresets().slice(0, 3)"
        (newGoal)="focusQuickGoal()"
        (openGoal)="goToGoal($event)"
        (applyPreset)="applyGoalPreset($event)"
      ></app-dashboard-personal-workspace>

      @if (isHintVisible('dashboard-start')) {
        <app-explanation-notice class="block mb-md inline-help" title="Kurzer Tipp" message="Beginne mit einem Ziel. Details wie Team, Worker oder Policies kannst du spaeter verfeinern.">
          <button class="secondary btn-small" type="button" (click)="dismissHint('dashboard-start')">Ausblenden</button>
        </app-explanation-notice>
      }

      <div class="start-actions mb-md">
        <app-action-card title="Ziel planen" description="Ein Satz reicht fuer den ersten Plan." href="#quick-goal"></app-action-card>
        <app-action-card title="Demo ansehen" description="Beispiele ohne echte Datenmutation." (action)="loadDemoPreview()"></app-action-card>
        <app-action-card title="Aufgaben verfolgen" description="Board, Status und naechste Schritte." [routerLink]="['/board']"></app-action-card>
        <app-action-card title="Ergebnisse ansehen" description="Artefakte und Resultate pruefen." [routerLink]="['/artifacts']"></app-action-card>
      </div>

      @if (demoPreview || demoLoading || demoError) {
        <app-dashboard-demo-preview
          [examples]="demoPreview?.examples || []"
          [loading]="demoLoading"
          [error]="demoError"
          [busy]="quickGoalBusy"
          (close)="closeDemoPreview()"
          (retry)="loadDemoPreview()"
          (startExample)="startDemoExample($event)"
        ></app-dashboard-demo-preview>
      }

      <section class="card card-primary mb-md" id="quick-goal">
        <h3 class="no-margin">Ziel planen</h3>
        <p class="muted font-sm mt-sm">Starte einfach mit einem Ziel. Gefuehrte Modi bleiben fuer strukturierte Faelle verfuegbar.</p>
        @if (isHintVisible('quick-goal')) {
          <app-explanation-notice class="block mt-sm inline-help" message='Ein gutes Ziel beschreibt Ergebnis und Grenze, zum Beispiel: "Analysiere nur das Frontend und schlage drei naechste Schritte vor."'>
            <button class="secondary btn-small" type="button" (click)="dismissHint('quick-goal')">Ausblenden</button>
          </app-explanation-notice>
        }

        <app-preset-picker
          class="block mt-sm"
          [presets]="goalPresetOptions()"
          ariaLabel="Goal-Vorlagen"
          (selectPreset)="applyGoalPresetById($event.id)"
        ></app-preset-picker>

        <div class="row gap-sm mt-sm flex-end">
          <div class="flex-1">
            <app-form-field label="Quick Goal" hint="Ein Satz reicht fuer den ersten planbaren Hub-Auftrag.">
              <input
                [(ngModel)]="quickGoalText"
                placeholder="z.B. Analysiere dieses Repository und schlage die naechsten Schritte vor"
                class="w-full"
                aria-label="Quick Goal Beschreibung eingeben"
                #quickGoalInput
              />
            </app-form-field>
          </div>
          <button (click)="submitQuickGoal()" [disabled]="quickGoalBusy || !quickGoalText.trim()" aria-label="Goal planen und Tasks generieren">
            @if (quickGoalBusy) {
              Generiere...
            } @else {
              Goal planen
            }
          </button>
          <button class="secondary" [routerLink]="['/auto-planner']" aria-label="Zur Auto-Planner Konfiguration navigieren">Mehr Optionen</button>
        </div>
        @if (quickGoalResult) {
          <app-safety-notice class="block mt-sm" title="Goal wurde geplant" [message]="quickGoalResult.tasks_created + ' Tasks erstellt.'" tone="success"></app-safety-notice>
          <div class="card-success mt-sm">
            <div class="row space-between">
              <span><strong>{{ quickGoalResult.tasks_created }}</strong> Tasks erstellt</span>
              <div class="row gap-sm">
                @if (quickGoalResult.goal_id) {
                  <button class="secondary btn-small" (click)="goToGoal(quickGoalResult.goal_id)">Zum Goal Detail</button>
                }
                <button class="secondary btn-small" (click)="goToBoard()">Zum Board</button>
              </div>
            </div>
            @if (quickGoalResult.task_ids?.length) {
              <div class="muted status-text-sm">
                Task IDs: {{ quickGoalResult.task_ids.slice(0, 3).join(', ') }}{{ quickGoalResult.task_ids.length > 3 ? '...' : '' }}
              </div>
            }
          </div>
          <app-next-steps class="block mt-sm" [steps]="quickGoalNextSteps()" (selectStep)="handleQuickGoalNextStep($event)"></app-next-steps>
        }

        <div style="margin: 20px 0; border-top: 1px solid rgba(255,255,255,0.1);"></div>

        <h3 class="no-margin">Gefuehrter Ziel-Assistent</h3>
        <p class="muted font-sm mt-sm">Der Assistent fragt nur die Angaben ab, die dem Hub beim Planen, Zuweisen und Pruefen helfen.</p>

        @if (!selectedGoalMode) {
          <app-mode-card-picker
            class="block mt-sm"
            [options]="goalModes"
            [columns]="4"
            ariaLabel="Goal-Modus auswaehlen"
            (selectOption)="setGoalMode($event)"
          ></app-mode-card-picker>
        } @else {
          <app-wizard-shell
            class="block mt-sm guided-goal-card"
            [title]="selectedGoalMode.title"
            [steps]="goalWizardSteps"
            [activeIndex]="goalWizardStepIndex"
            [canContinue]="canContinueGoalWizard()"
            [busy]="quickGoalBusy"
            submitLabel="Goal planen"
            busyLabel="Plane..."
            ariaLabel="Gefuehrte Zielerstellung"
            (stepSelect)="goToGoalWizardStep($event)"
            (previous)="previousGoalWizardStep()"
            (next)="nextGoalWizardStep()"
            (submit)="submitGuidedGoal()"
          >
            <button wizard-actions class="secondary btn-small" (click)="setGoalMode(null)">Zurueck</button>
              @if (activeGoalWizardStep().id === 'goal') {
                <div class="grid gap-sm">
                  @for (field of requiredGoalFields(); track field.name) {
                    <app-form-field [label]="field.label" [hint]="fieldHelper(field.name)" [required]="true">
                      @if (field.type === 'textarea') {
                        <textarea [(ngModel)]="goalModeData[field.name]" class="w-full" rows="3" style="min-height: 88px;" [placeholder]="field.placeholder || 'Beschreibe, was erreicht werden soll.'"></textarea>
                      } @else if (field.type === 'select') {
                        <select [(ngModel)]="goalModeData[field.name]" class="w-full">
                          @for (opt of field.options; track opt) {
                            <option [value]="opt">{{ opt }}</option>
                          }
                        </select>
                      } @else {
                        <input [(ngModel)]="goalModeData[field.name]" [type]="field.type" [placeholder]="field.placeholder || ''" class="w-full" />
                      }
                    </app-form-field>
                  }
                </div>
              } @else if (activeGoalWizardStep().id === 'context') {
                <app-form-field label="Kontext und Eingabedaten" hint="Mehr Kontext reduziert Rueckfragen und hilft dem Hub, Tasks an passende Worker zu geben.">
                  <textarea [(ngModel)]="goalModeData['context']" class="w-full" rows="5" placeholder="Links, Dateien, Fehlermeldungen, Repo-Bereich oder wichtige Einschraenkungen"></textarea>
                </app-form-field>
              } @else if (activeGoalWizardStep().id === 'execution') {
                <div class="grid cols-3 gap-sm">
                  @for (option of executionDepthOptions; track option.value) {
                    <button type="button" class="card card-light wizard-choice text-left" [class.active]="goalModeData['execution_depth'] === option.value" (click)="goalModeData['execution_depth'] = option.value">
                      <strong>{{ option.label }}</strong>
                      <span>{{ option.description }}</span>
                    </button>
                  }
                </div>
              } @else if (activeGoalWizardStep().id === 'safety') {
                <div class="grid cols-3 gap-sm">
                  @for (option of safetyLevelOptions; track option.value) {
                    <button type="button" class="card card-light wizard-choice text-left" [class.active]="goalModeData['safety_level'] === option.value" (click)="goalModeData['safety_level'] = option.value">
                      <strong>{{ option.label }}</strong>
                      <span>{{ option.description }}</span>
                    </button>
                  }
                </div>
              } @else {
                <app-explanation-notice title="Bereit zum Planen" message="Der Hub erstellt daraus planbare Tasks. Worker fuehren die delegierten Schritte aus; Pruefungen und Freigaben bleiben sichtbar."></app-explanation-notice>
                <div class="grid cols-2 gap-sm mt-sm">
                  <div class="card card-light">
                    <div class="muted font-sm">Ausfuehrung</div>
                    <strong>{{ selectedExecutionDepthLabel() }}</strong>
                  </div>
                  <div class="card card-light">
                    <div class="muted font-sm">Sicherheit</div>
                    <strong>{{ selectedSafetyLevelLabel() }}</strong>
                  </div>
                </div>
              }
          </app-wizard-shell>
        }
      </section>
    }

    @if (stats) {
      <app-onboarding-checklist />
      <div class="grid cols-3">
        <app-section-card title="Agenten">
          <app-key-value-grid [items]="agentSummaryItems()" [columns]="2"></app-key-value-grid>
        </app-section-card>
        <app-section-card title="Tasks">
          <app-key-value-grid [items]="taskSummaryItems()" [columns]="2"></app-key-value-grid>
        </app-section-card>
        <app-system-status-summary
          [systemStatus]="systemStatusLabel()"
          [systemTone]="systemStatusTone()"
          [liveConnected]="liveState.systemStreamConnected()"
          [tasksLoading]="tasksLoading()"
          [taskCollectionError]="taskCollectionError()"
          [tasksLastLoadedAt]="tasksLastLoadedAt()"
          [lastSystemEventType]="liveState.lastSystemEvent()?.type || ''"
          [queueDepth]="systemHealth?.checks?.queue ? (systemHealth?.checks?.queue?.depth || 0) : null"
          [registrationEnabled]="!!systemHealth?.checks?.registration?.enabled"
          [registrationStatus]="systemHealth?.checks?.registration?.status || ''"
          [registrationAttempts]="systemHealth?.checks?.registration?.attempts || 0"
          [schedulerKnown]="!!systemHealth?.checks?.scheduler"
          [schedulerRunning]="!!systemHealth?.checks?.scheduler?.running"
          [schedulerJobCount]="systemHealth?.checks?.scheduler?.scheduled_count || 0"
          [contractsVersion]="contracts?.version || ''"
          [contractsSchemaCount]="contracts?.schema_count || 0"
          [taskStates]="contracts?.task_statuses?.canonical_values || []"
          [activeTeamName]="activeTeam?.name || ''"
          [teamMembers]="activeTeamMembers()"
          [hubName]="stats.agent_name"
          [timestamp]="stats.timestamp"
        ></app-system-status-summary>
      </div>
      <div class="row mt-sm">
        <button class="secondary btn-small" (click)="toggleAdvancedDashboard()">
          {{ showAdvancedDashboard ? 'Technische Details ausblenden' : 'Technische Details anzeigen' }}
        </button>
      </div>
    }

    @if (hub && showAdvancedDashboard) {
      <div class="grid cols-2 mb-md">
        <div class="card">
          <h3>Shell Pool</h3>
          <div class="row space-between">
            <span>Gesamt:</span>
            <strong>{{stats?.shell_pool?.total || 0}}</strong>
          </div>
          <div class="row space-between">
            <span>Frei:</span>
            <strong class="success">{{stats?.shell_pool?.free || 0}}</strong>
          </div>
          <div class="row space-between">
            <span>Belegt:</span>
            <strong [class.danger]="(stats?.shell_pool?.busy || 0) > 0">{{stats?.shell_pool?.busy || 0}}</strong>
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
      </div>

      <app-dashboard-benchmark-panel
        [data]="benchmarkData"
        [updatedAt]="benchmarkUpdatedAt"
        [recommendation]="benchmarkRecommendation"
        [llmDefaults]="llmDefaults"
        [llmExplicitOverride]="llmExplicitOverride"
        [llmEffectiveRuntime]="llmEffectiveRuntime"
        [hubCopilotStatus]="hubCopilotStatus"
        [contextPolicyStatus]="contextPolicyStatus"
        [artifactFlowStatus]="artifactFlowStatus"
        [researchBackendStatus]="researchBackendStatus"
        [runtimeTelemetry]="runtimeTelemetry"
        [(taskKind)]="benchmarkTaskKind"
        (refresh)="refreshBenchmarks()"
      ></app-dashboard-benchmark-panel>

      <app-dashboard-goal-governance-summary-card
        [goals]="goalsList"
        [selectedGoalId]="selectedGoalId"
        [loading]="goalReportingLoading"
        [goalDetail]="goalDetail"
        [goalGovernance]="goalGovernance"
        [costTasks]="goalCostTasks()"
        (selectGoal)="refreshGoalReporting($event)"
        (refresh)="refreshGoalReporting($event)"
      ></app-dashboard-goal-governance-summary-card>

      <app-dashboard-autopilot-panel
        [status]="autopilotStatus"
        [teams]="teamsList"
        [busy]="autopilotBusy"
        [(goal)]="autopilotGoal"
        [(teamId)]="autopilotTeamId"
        [(intervalSeconds)]="autopilotIntervalSeconds"
        [(maxConcurrency)]="autopilotMaxConcurrency"
        [(budgetLabel)]="autopilotBudgetLabel"
        [(securityLevel)]="autopilotSecurityLevel"
        (start)="startAutopilot()"
        (stop)="stopAutopilot()"
        (tick)="tickAutopilot()"
        (refresh)="refreshAutopilot()"
      ></app-dashboard-autopilot-panel>

      <app-dashboard-timeline-panel
        [items]="taskTimeline"
        [teams]="teamsList"
        [agents]="agentsList"
        [(teamId)]="timelineTeamId"
        [(agent)]="timelineAgent"
        [(status)]="timelineStatus"
        [(errorOnly)]="timelineErrorOnly"
        (refresh)="refreshTaskTimeline()"
      ></app-dashboard-timeline-panel>
    }

    @if (history.length > 1 && showAdvancedDashboard) {
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

    @if (agentsList.length > 0 && showAdvancedDashboard) {
      <app-dashboard-agent-status-panel [agents]="agentsList"></app-dashboard-agent-status-panel>
    }

    @if (!stats && hub && viewState.loading) {
      <div class="card">
        <p>Lade Statistiken von Hub ({{hub.url}})...</p>
      </div>
    }

    @if (!hub) {
      <div class="state-banner error">
        <strong>Kein Hub-Agent konfiguriert.</strong>
        <p class="muted mt-sm">
          Ananta braucht einen Hub als zentrale Steuerung. Fuege einen Agenten mit Rolle "hub" hinzu oder pruefe den lokalen Start.
        </p>
        <button [routerLink]="['/agents']">Agenten verwalten</button>
      </div>
    }
  `
})
export class DashboardComponent implements OnInit, OnDestroy {
  private dir = inject(AgentDirectoryService);
  protected ns = inject(NotificationService);
  private toast = inject(ToastService);
  private router = inject(Router);
  private refreshRuntime = inject(DashboardRefreshRuntimeService);
  protected hubApi = inject(ControlPlaneFacade);
  protected taskFacade = inject(TaskManagementFacade);
  private facade = inject(DashboardFacade);
  private goalReporting = inject(DashboardGoalReportingFacade);
  private workspaceViewModel = inject(DashboardWorkspaceViewModelService);
  readonly liveState = this.hubApi;

  hub = this.dir.list().find(a => a.role === 'hub');

  // Daten-/ViewState-Felder delegieren an DashboardFacade.
  get stats(): DashboardStatsBlock | null { return this.facade.stats; }
  set stats(v: DashboardStatsBlock | null) { this.facade.stats = v; }
  get systemHealth(): SystemHealth | null { return this.facade.systemHealth; }
  set systemHealth(v: SystemHealth | null) { this.facade.systemHealth = v; }
  get contracts(): ContractsStatus | null { return this.facade.contracts; }
  set contracts(v: ContractsStatus | null) { this.facade.contracts = v; }
  get history(): unknown[] { return this.facade.history; }
  set history(v: unknown[]) { this.facade.history = v; }
  get agentsList(): AgentEntry[] { return this.facade.agents; }
  set agentsList(v: AgentEntry[]) { this.facade.agents = v; }
  get teamsList(): TeamEntry[] { return this.facade.teams; }
  set teamsList(v: TeamEntry[]) { this.facade.teams = v; }
  get activeTeam(): TeamEntry | null { return this.facade.activeTeam; }
  set activeTeam(v: TeamEntry | null) { this.facade.activeTeam = v; }
  get roles(): RoleEntry[] { return this.facade.roles; }
  set roles(v: RoleEntry[]) { this.facade.roles = v; }
  get taskTimeline(): TimelineEvent[] { return this.facade.taskTimeline; }
  set taskTimeline(v: TimelineEvent[]) { this.facade.taskTimeline = v; }
  get benchmarkData(): BenchmarkItem[] { return this.facade.benchmarkData; }
  set benchmarkData(v: BenchmarkItem[]) { this.facade.benchmarkData = v; }
  get benchmarkUpdatedAt(): number | null { return this.facade.benchmarkUpdatedAt; }
  set benchmarkUpdatedAt(v: number | null) { this.facade.benchmarkUpdatedAt = v; }
  get benchmarkRecommendation(): BenchmarkRecommendation | null { return this.facade.benchmarkRecommendation; }
  set benchmarkRecommendation(v: BenchmarkRecommendation | null) { this.facade.benchmarkRecommendation = v; }
  get llmDefaults(): LlmModelReference | null { return this.facade.llmDefaults; }
  set llmDefaults(v: LlmModelReference | null) { this.facade.llmDefaults = v; }
  get llmExplicitOverride(): LlmModelReference | null { return this.facade.llmExplicitOverride; }
  set llmExplicitOverride(v: LlmModelReference | null) { this.facade.llmExplicitOverride = v; }
  get llmEffectiveRuntime(): LlmEffectiveRuntime | null { return this.facade.llmEffectiveRuntime; }
  set llmEffectiveRuntime(v: LlmEffectiveRuntime | null) { this.facade.llmEffectiveRuntime = v; }
  get hubCopilotStatus(): HubCopilotStatus | null { return this.facade.hubCopilotStatus; }
  set hubCopilotStatus(v: HubCopilotStatus | null) { this.facade.hubCopilotStatus = v; }
  get contextPolicyStatus(): ContextPolicyStatus | null { return this.facade.contextPolicyStatus; }
  set contextPolicyStatus(v: ContextPolicyStatus | null) { this.facade.contextPolicyStatus = v; }
  get artifactFlowStatus(): ArtifactFlowStatus | null { return this.facade.artifactFlowStatus; }
  set artifactFlowStatus(v: ArtifactFlowStatus | null) { this.facade.artifactFlowStatus = v; }
  get researchBackendStatus(): ResearchBackendStatus | null { return this.facade.researchBackendStatus; }
  set researchBackendStatus(v: ResearchBackendStatus | null) { this.facade.researchBackendStatus = v; }
  get runtimeTelemetry(): RuntimeTelemetry | null { return this.facade.runtimeTelemetry; }
  set runtimeTelemetry(v: RuntimeTelemetry | null) { this.facade.runtimeTelemetry = v; }
  get viewState(): UiAsyncState { return this.facade.viewState; }
  set viewState(v: UiAsyncState) { this.facade.viewState = v; }

  autopilotStatus: AutopilotStatus | null = null;
  autopilotBusy = false;
  autopilotGoal = '';
  autopilotTeamId = '';
  autopilotIntervalSeconds = 20;
  autopilotMaxConcurrency = 2;
  autopilotBudgetLabel = '';
  autopilotSecurityLevel: AutopilotSecurityLevel = 'safe';
  get benchmarkTaskKind(): BenchmarkTaskKind { return this.facade.benchmarkTaskKind; }
  set benchmarkTaskKind(v: BenchmarkTaskKind) { this.facade.benchmarkTaskKind = v; }
  get goalsList(): GoalListEntry[] { return this.goalReporting.state.goals; }
  get selectedGoalId(): string { return this.goalReporting.state.selectedGoalId; }
  get goalDetail() { return this.goalReporting.state.goalDetail; }
  get goalGovernance() { return this.goalReporting.state.goalGovernance; }
  get goalReportingLoading(): boolean { return this.goalReporting.state.loading; }
  goalModes: GoalModeDefinition[] = [];
  selectedGoalMode: GoalModeDefinition | null = null;
  goalModeData: Record<string, unknown> = {};
  goalWizardStepIndex = 0;
  goalWizardSteps: GoalWizardStep[] = [
    { id: 'goal', title: 'Ziel', helper: 'Beschreibe, was am Ende anders oder besser sein soll.' },
    { id: 'context', title: 'Kontext', helper: 'Ergaenze Daten, Grenzen oder Fundstellen, damit weniger Rueckfragen entstehen.' },
    { id: 'execution', title: 'Tiefe', helper: 'Waehle, wie gruendlich der Hub planen und Tasks erzeugen soll.' },
    { id: 'safety', title: 'Sicherheit', helper: 'Lege fest, wie vorsichtig Ananta mit Freigaben und Pruefung umgehen soll.' },
    { id: 'review', title: 'Pruefen', helper: 'Kontrolliere die Angaben, bevor der Hub Tasks erstellt.' },
  ];
  executionDepthOptions = [
    { value: 'quick', label: 'Schnell', description: 'Kleiner Plan mit wenigen Tasks fuer einfache Ziele.' },
    { value: 'standard', label: 'Standard', description: 'Ausgewogener Plan mit Kontext, Umsetzung und Pruefung.' },
    { value: 'deep', label: 'Gruendlich', description: 'Mehr Analyse, klarere Risiken und staerkere Nachweise.' },
  ];
  safetyLevelOptions = [
    { value: 'safe', label: 'Vorsichtig', description: 'Mehr Review und keine riskanten automatischen Schritte.' },
    { value: 'balanced', label: 'Ausgewogen', description: 'Normale Freigaben und sichtbare Pruefpunkte.' },
    { value: 'fast', label: 'Schneller', description: 'Weniger Reibung fuer harmlose lokale Aufgaben.' },
  ];
  firstStartOptions: ModeCardOption[] = [
    { id: 'demo', title: 'Demo ansehen', description: 'Beispiele lesen und bei Bedarf als echte Goals starten.' },
    { id: 'goal', title: 'Eigenes Ziel planen', description: 'Mit einem Satz starten und Tasks erzeugen lassen.' },
    { id: 'board', title: 'Leer starten', description: 'Direkt ins Board und Aufgaben manuell anlegen.' },
  ];
  timelineTeamId = '';
  timelineAgent = '';
  timelineStatus = '';
  timelineErrorOnly = false;
  quickGoalText = '';
  quickGoalContext = '';
  quickGoalBusy = false;
  quickGoalResult: { tasks_created: number; task_ids: string[]; goal_id?: string } | null = null;
  demoPreview: { examples?: DemoPreviewExample[] } | null = null;
  demoLoading = false;
  demoError = '';
  showFirstStartWizard = localStorage.getItem('ananta.first-start.completed') !== 'true';
  showAdvancedDashboard = localStorage.getItem('ananta.dashboard.advanced') === 'true';
  hiddenHints = new Set<string>((localStorage.getItem('ananta.hidden-hints') || '').split(',').filter(Boolean));
  private connectedTaskCollectionHubUrl: string | null = null;

  ngOnInit() {
    if (this.hub?.url) this.ensureTaskCollection();
    this.refreshGoalModes();
    this.refreshRuntime.start(() => this.refresh(), 10000);
  }

  ngOnDestroy() {
    this.refreshRuntime.stop();
    this.taskFacade.disconnectTaskCollection(this.hub?.url);
    this.connectedTaskCollectionHubUrl = null;
    this.facade.dispose();
  }

  refresh() {
    if (!this.hub) {
      this.hub = this.dir.list().find(a => a.role === 'hub');
    }
    if (!this.hub) return;
    this.liveState.ensureSystemEvents(this.hub.url);
    this.ensureTaskCollection();

    this.facade.refresh(this.hub.url, this.benchmarkTaskKind);
    this.refreshAutopilot();
    this.refreshGoalReporting();
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

  refreshGoalModes() {
    if (!this.hub) return;
    this.hubApi.listGoalModes(this.hub.url).subscribe({
      next: modes => this.goalModes = modes,
      error: () => this.ns.error('Goal-Modi konnten nicht geladen werden')
    });
  }

  setGoalMode(mode: any) {
    this.selectedGoalMode = mode;
    this.goalModeData = {};
    this.goalWizardStepIndex = 0;
    if (mode) {
      mode.fields?.forEach((f: any) => {
        if (f.default !== undefined) this.goalModeData[f.name] = f.default;
      });
      this.goalModeData['execution_depth'] = this.goalModeData['execution_depth'] || 'standard';
      this.goalModeData['safety_level'] = this.goalModeData['safety_level'] || 'balanced';
    }
  }

  activeGoalWizardStep(): GoalWizardStep {
    return this.goalWizardSteps[this.goalWizardStepIndex] || this.goalWizardSteps[0];
  }

  goToGoalWizardStep(index: number): void {
    if (index < 0 || index >= this.goalWizardSteps.length) return;
    this.goalWizardStepIndex = index;
  }

  nextGoalWizardStep(): void {
    if (!this.canContinueGoalWizard()) return;
    this.goalWizardStepIndex = Math.min(this.goalWizardStepIndex + 1, this.goalWizardSteps.length - 1);
  }

  previousGoalWizardStep(): void {
    this.goalWizardStepIndex = Math.max(this.goalWizardStepIndex - 1, 0);
  }

  isLastGoalWizardStep(): boolean {
    return this.goalWizardStepIndex >= this.goalWizardSteps.length - 1;
  }

  canContinueGoalWizard(): boolean {
    const step = this.activeGoalWizardStep().id;
    if (step === 'goal') return this.requiredGoalFields().every(field => String(this.goalModeData[field.name] || '').trim().length > 0);
    if (step === 'execution') return !!this.goalModeData['execution_depth'];
    if (step === 'safety') return !!this.goalModeData['safety_level'];
    return true;
  }

  requiredGoalFields(): GoalModeField[] {
    return (this.selectedGoalMode?.fields || []).filter(field => field.type !== 'hidden');
  }

  fieldHelper(name: string): string {
    const normalized = String(name || '').toLowerCase();
    if (normalized.includes('goal') || normalized.includes('ziel')) return 'Ein klares Ziel hilft dem Hub, daraus pruefbare Tasks zu bilden.';
    if (normalized.includes('context') || normalized.includes('kontext')) return 'Kontext verhindert falsche Annahmen und hilft bei der Worker-Zuweisung.';
    if (normalized.includes('team')) return 'Optional: Teams koennen spaeter auch im Board oder in Expertenbereichen gesetzt werden.';
    return 'Diese Angabe strukturiert den Plan und macht das Ergebnis besser pruefbar.';
  }

  selectedExecutionDepthLabel(): string {
    const selected = this.executionDepthOptions.find(option => option.value === this.goalModeData['execution_depth']);
    return selected?.label || 'Standard';
  }

  selectedSafetyLevelLabel(): string {
    const selected = this.safetyLevelOptions.find(option => option.value === this.goalModeData['safety_level']);
    return selected?.label || 'Ausgewogen';
  }

  isHintVisible(key: string): boolean {
    return !this.hiddenHints.has(key);
  }

  dismissHint(key: string): void {
    this.hiddenHints.add(key);
    localStorage.setItem('ananta.hidden-hints', Array.from(this.hiddenHints).join(','));
  }

  submitGuidedGoal() {
    if (!this.hub || !this.selectedGoalMode) return;
    this.quickGoalBusy = true;
    this.quickGoalResult = null;

    this.hubApi.createGoal(this.hub.url, {
      mode: this.selectedGoalMode.id,
      mode_data: {
        ...this.goalModeData,
        wizard: {
          execution_depth: this.goalModeData['execution_depth'] || 'standard',
          safety_level: this.goalModeData['safety_level'] || 'balanced',
          context: this.goalModeData['context'] || '',
        },
      },
      create_tasks: true
    }).subscribe({
      next: (result: any) => {
        this.quickGoalBusy = false;
        this.quickGoalResult = {
          tasks_created: result?.created_task_ids?.length || 0,
          task_ids: result?.created_task_ids || [],
          goal_id: result?.goal?.id
        };
        this.toast.success(`${this.quickGoalResult.tasks_created} Tasks erstellt`);
        this.selectedGoalMode = null;
        this.goalModeData = {};
        this.goalWizardStepIndex = 0;
        this.refresh();
      },
      error: (err) => {
        this.quickGoalBusy = false;
        this.ns.error('Gefuehrte Goal-Planung fehlgeschlagen: ' + (err.error?.message || err.message));
      }
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
    this.facade.refreshTaskTimeline(this.hub.url, {
      teamId: this.timelineTeamId,
      agent: this.timelineAgent,
      status: this.timelineStatus,
      errorOnly: this.timelineErrorOnly,
    });
  }

  refreshBenchmarks() {
    if (!this.hub) return;
    this.facade.refreshBenchmarks(this.hub.url, this.benchmarkTaskKind);
  }

  focusQuickGoal() {
    document.getElementById('quick-goal')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    window.setTimeout(() => {
      const input = document.querySelector<HTMLInputElement>('#quick-goal input');
      input?.focus();
    }, 150);
  }

  loadDemoPreview() {
    if (!this.hub) return;
    this.demoLoading = true;
    this.demoError = '';
    this.hubApi.getDemoPreview(this.hub.url).subscribe({
      next: preview => {
        this.demoPreview = preview;
        this.demoLoading = false;
      },
      error: err => {
        this.demoLoading = false;
        this.demoPreview = null;
        this.demoError = err?.error?.message || err?.message || 'Demo-Vorschau ist gerade nicht erreichbar.';
      }
    });
  }

  closeDemoPreview(): void {
    this.demoPreview = null;
    this.demoError = '';
  }

  goalPresets(): DemoPreviewExample[] {
    return this.demoPreview?.examples?.length ? this.demoPreview.examples : DEFAULT_GOAL_PRESETS;
  }

  goalPresetOptions(): PresetOption[] {
    return this.goalPresets().map(preset => ({
      id: preset.id,
      title: preset.title,
      description: preset.outcome,
    }));
  }

  applyGoalPreset(preset: DemoPreviewExample): void {
    this.quickGoalText = preset.goal;
    this.quickGoalContext = preset.starter_context || `Vorlage: ${preset.title}`;
    this.focusQuickGoal();
  }

  applyGoalPresetById(id: string): void {
    const preset = this.goalPresets().find(item => item.id === id);
    if (preset) this.applyGoalPreset(preset);
  }

  quickGoalNextSteps(): NextStepAction[] {
    return [
      {
        id: 'goal',
        label: 'Goal Detail oeffnen',
        description: 'Plan, Governance und Ergebnisstatus pruefen.',
        disabled: !this.quickGoalResult?.goal_id,
      },
      {
        id: 'board',
        label: 'Board oeffnen',
        description: 'Erzeugte Tasks verfolgen und naechste Arbeit starten.',
        routerLink: ['/board'],
      },
      {
        id: 'artifacts',
        label: 'Ergebnisse ansehen',
        description: 'Artefakte und spaetere Resultate pruefen.',
        routerLink: ['/artifacts'],
      },
    ];
  }

  handleQuickGoalNextStep(step: NextStepAction): void {
    if (step.id === 'goal' && this.quickGoalResult?.goal_id) this.goToGoal(this.quickGoalResult.goal_id);
  }

  chooseFirstStart(choice: string): void {
    this.completeFirstStartWizard();
    if (choice === 'demo') {
      this.loadDemoPreview();
      return;
    }
    if (choice === 'board') {
      this.router.navigate(['/board']);
      return;
    }
    this.focusQuickGoal();
  }

  completeFirstStartWizard(): void {
    localStorage.setItem('ananta.first-start.completed', 'true');
    this.showFirstStartWizard = false;
  }

  toggleAdvancedDashboard(): void {
    this.showAdvancedDashboard = !this.showAdvancedDashboard;
    localStorage.setItem('ananta.dashboard.advanced', String(this.showAdvancedDashboard));
  }

  startDemoExample(example: DemoPreviewExample): void {
    if (!this.hub || !example?.goal) return;
    this.quickGoalBusy = true;
    this.quickGoalResult = null;
    this.completeFirstStartWizard();
    this.hubApi.planGoal(this.hub.url, {
      goal: example.goal,
      context: example.starter_context || `Demo-Beispiel: ${example.title}`,
      create_tasks: true
    }).subscribe({
      next: (result: any) => {
        this.quickGoalBusy = false;
        this.quickGoalResult = {
          tasks_created: result?.created_task_ids?.length || 0,
          task_ids: result?.created_task_ids || [],
          goal_id: result?.goal_id
        };
        this.toast.success(`Demo-Goal gestartet: ${this.quickGoalResult.tasks_created} Tasks erstellt`);
        this.refresh();
      },
      error: () => {
        this.quickGoalBusy = false;
        this.toast.error('Demo-Goal konnte nicht gestartet werden');
      }
    });
  }

  tasksLastLoadedAt(): number | null {
    return this.taskFacade.tasksLastLoadedAt();
  }

  tasksLoading(): boolean {
    return this.taskFacade.tasksLoading();
  }

  taskCollectionError(): string | null {
    return this.taskFacade.taskCollectionError();
  }

  recentGoals(): GoalListEntry[] {
    return this.goalReporting.recentGoals(3);
  }

  activeGoalCount(): number {
    return this.goalReporting.activeGoalCount();
  }

  nextTaskCount(): number {
    return this.workspaceViewModel.nextTaskCount(typeof this.taskFacade.tasks === 'function' ? this.taskFacade.tasks() : []);
  }

  starterProgress(): { done: number; total: number; label: string } {
    return this.workspaceViewModel.starterProgress({
      firstStartCompleted: this.showFirstStartWizard === false,
      goals: this.goalsList,
      hasQuickGoalResult: Boolean(this.quickGoalResult),
      nextTaskCount: this.nextTaskCount(),
      createdTaskCount: Number(this.quickGoalResult?.tasks_created || 0),
    });
  }

  agentSummaryItems(): KeyValueItem[] {
    return [
      { label: 'Gesamt', value: this.stats?.agents?.total || 0 },
      { label: 'Online', value: this.stats?.agents?.online || 0 },
      { label: 'Offline', value: this.stats?.agents?.offline || 0 },
    ];
  }

  taskSummaryItems(): KeyValueItem[] {
    return [
      { label: 'Gesamt', value: this.stats?.tasks?.total || 0 },
      { label: 'Abgeschlossen', value: this.stats?.tasks?.completed || 0 },
      { label: 'Fehlgeschlagen', value: this.stats?.tasks?.failed || 0 },
      { label: 'In Arbeit', value: this.stats?.tasks?.in_progress || 0 },
    ];
  }

  systemStatusLabel(): string {
    return String(this.systemHealth?.status || ((this.stats?.agents?.online || 0) > 0 ? 'ok' : 'degraded'));
  }

  systemStatusTone(): StatusTone {
    const label = this.systemStatusLabel().toLowerCase();
    if (label === 'ok') return 'success';
    if (label === 'degraded') return 'warning';
    if (label === 'error' || label === 'failed') return 'error';
    return 'unknown';
  }

  private ensureTaskCollection(): void {
    if (!this.hub?.url) return;
    if (this.connectedTaskCollectionHubUrl && this.connectedTaskCollectionHubUrl !== this.hub.url) {
      this.taskFacade.disconnectTaskCollection(this.connectedTaskCollectionHubUrl);
      this.connectedTaskCollectionHubUrl = null;
    }
    if (this.connectedTaskCollectionHubUrl === this.hub.url) return;
    this.liveState.ensureSystemEvents(this.hub.url);
    this.taskFacade.connectTaskCollection(this.hub.url, 10000);
    this.connectedTaskCollectionHubUrl = this.hub.url;
  }

  refreshGoalReporting(goalId?: string) {
    if (!this.hub) return;
    this.goalReporting.refresh(this.hub.url, goalId);
  }

  goalCostTasks(): any[] {
    return this.goalReporting.costTasks();
  }

  researchBackendProviderEntries(): any[] {
    const providers = this.researchBackendStatus?.providers;
    if (!providers || typeof providers !== 'object') return [];
    return Object.values(providers) as any[];
  }

  activeInferenceRuntime(): any | null {
    const runtimeProviders = this.runtimeTelemetry?.providers;
    if (!runtimeProviders || typeof runtimeProviders !== 'object') return null;
    const provider = String(
      this.llmEffectiveRuntime?.provider ||
      this.llmDefaults?.provider ||
      this.llmExplicitOverride?.provider ||
      ''
    ).trim().toLowerCase();
    if (!provider) return null;

    const model = String(
      this.llmEffectiveRuntime?.model ||
      this.llmDefaults?.model ||
      this.llmExplicitOverride?.model ||
      ''
    ).trim();
    const providerState: any = (runtimeProviders as Record<string, any>)?.[provider] || null;
    if (!providerState) return null;

    const contextLength = this.resolveContextLength(provider, model, providerState);
    const ollamaActivity = provider === 'ollama' ? (providerState?.activity || null) : null;
    const executorSummary = ollamaActivity?.executor_summary || {};
    const gpuActive = ollamaActivity?.gpu_active;
    const temperatureRaw = this.llmEffectiveRuntime?.temperature ?? this.hubCopilotStatus?.effective?.temperature ?? null;
    const temperature = Number.isFinite(Number(temperatureRaw)) ? Number(temperatureRaw) : null;
    const activeEntry = provider === 'ollama'
      ? ((Array.isArray(ollamaActivity?.active_models) ? ollamaActivity.active_models : []).find((item: any) => String(item?.name || '').trim() === model) || null)
      : null;
    const executor = String(activeEntry?.executor || '').trim().toLowerCase();
    const executorLabel = executor ? executor.toUpperCase() : (
      Number(executorSummary?.gpu || 0) > 0 ? 'GPU'
      : Number(executorSummary?.cpu || 0) > 0 ? 'CPU'
      : 'unknown'
    );

    return {
      provider,
      model: model || '-',
      contextLengthLabel: contextLength ? `${contextLength} tokens` : 'unknown',
      temperatureLabel: temperature === null ? 'default' : temperature.toFixed(2),
      executorLabel,
      gpuActiveLabel: gpuActive === true ? 'yes' : gpuActive === false ? 'no' : 'unknown',
      providerStatus: String(providerState?.status || 'unknown'),
      providerReachableLabel: providerState?.reachable === true ? 'yes' : providerState?.reachable === false ? 'no' : 'unknown',
      candidateCountLabel: String(Number(providerState?.candidate_count || 0)),
      telemetrySource: provider === 'ollama' ? '/api/tags + /api/ps' : '/v1/models',
    };
  }

  liveRuntimeModels(): any[] {
    const runtimeProviders = this.runtimeTelemetry?.providers;
    if (!runtimeProviders || typeof runtimeProviders !== 'object') return [];
    const rows: any[] = [];
    const activeProvider = String(this.llmEffectiveRuntime?.provider || '').trim().toLowerCase();
    const activeModel = String(this.llmEffectiveRuntime?.model || '').trim();

    const ollama = runtimeProviders?.ollama;
    if (ollama && typeof ollama === 'object') {
      const ollamaModels = Array.isArray(ollama?.models) ? ollama.models : [];
      const activeModels = Array.isArray(ollama?.activity?.active_models) ? ollama.activity.active_models : [];
      for (const entry of activeModels) {
        const model = String(entry?.name || '').trim();
        if (!model) continue;
        const modelDef = ollamaModels.find((item: any) => String(item?.name || '').trim() === model) || null;
        const contextLength = Number(
          entry?.context_length ||
          entry?.num_ctx ||
          modelDef?.context_length ||
          modelDef?.num_ctx ||
          modelDef?.details?.context_length ||
          modelDef?.details?.num_ctx ||
          0
        );
        const contextLengthLabel = Number.isFinite(contextLength) && contextLength > 0 ? `${contextLength} tokens` : 'unknown';
        const executor = String(entry?.executor || '').trim().toLowerCase();
        rows.push({
          id: `ollama:${model}:${executor || 'unknown'}`,
          provider: 'ollama',
          model,
          executorLabel: executor ? executor.toUpperCase() : 'unknown',
          contextLengthLabel,
          statusLabel: activeProvider === 'ollama' && activeModel === model ? 'active runtime' : 'active',
          sourceLabel: '/api/ps',
        });
      }
    }

    const lmstudio = runtimeProviders?.lmstudio;
    if (lmstudio && typeof lmstudio === 'object') {
      const candidates = Array.isArray(lmstudio?.candidates) ? lmstudio.candidates : [];
      for (const entry of candidates) {
        const model = String(entry?.id || entry?.name || '').trim();
        if (!model) continue;
        const contextLength = Number(entry?.context_length || entry?.num_ctx || 0);
        const contextLengthLabel = Number.isFinite(contextLength) && contextLength > 0 ? `${contextLength} tokens` : 'unknown';
        const isActiveRuntime = activeProvider === 'lmstudio' && activeModel === model;
        const loaded = entry?.loaded === true;
        rows.push({
          id: `lmstudio:${model}`,
          provider: 'lmstudio',
          model,
          executorLabel: loaded ? 'loaded' : 'unknown',
          contextLengthLabel,
          statusLabel: isActiveRuntime ? 'active runtime' : (loaded ? 'loaded' : 'available'),
          sourceLabel: '/v1/models',
        });
      }
    }

    rows.sort((left: any, right: any) => {
      const leftActive = String(left?.statusLabel || '').includes('active') ? 1 : 0;
      const rightActive = String(right?.statusLabel || '').includes('active') ? 1 : 0;
      if (leftActive !== rightActive) return rightActive - leftActive;
      return String(left?.model || '').localeCompare(String(right?.model || ''));
    });
    return rows;
  }

  private resolveContextLength(provider: string, model: string, providerState: any): number | null {
    if (!model) return null;
    if (provider === 'lmstudio') {
      const candidates = Array.isArray(providerState?.candidates) ? providerState.candidates : [];
      const candidate = candidates.find((item: any) => String(item?.id || '').trim() === model);
      const value = Number(candidate?.context_length || 0);
      return Number.isFinite(value) && value > 0 ? value : null;
    }
    if (provider === 'ollama') {
      const models = Array.isArray(providerState?.models) ? providerState.models : [];
      const item = models.find((entry: any) => String(entry?.name || '').trim() === model);
      const value = Number(
        item?.context_length ||
        item?.num_ctx ||
        item?.details?.context_length ||
        item?.details?.num_ctx ||
        0
      );
      return Number.isFinite(value) && value > 0 ? value : null;
    }
    return null;
  }

  getPoints(type: 'completed' | 'failed' | 'cpu' | 'ram'): string {
    const history = this.history as Array<{
      tasks?: { total?: number; completed?: number; failed?: number };
      resources?: { cpu_percent?: number; ram_bytes?: number };
    }>;
    if (history.length < 2) return '';

    let maxVal = 1;
    if (type === 'completed' || type === 'failed') {
      maxVal = Math.max(...history.map(h => h.tasks?.total || 1), 1);
    } else if (type === 'cpu') {
      maxVal = 100;
    } else if (type === 'ram') {
      maxVal = Math.max(...history.map(h => h.resources?.ram_bytes || 1), 1);
    }

    const stepX = 1000 / (history.length - 1);

    return history.map((h, i) => {
      let val = 0;
      if (type === 'completed' || type === 'failed') {
        val = Number(h.tasks?.[type] || 0);
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

  activeTeamMembers(): SystemStatusTeamMember[] {
    return (this.activeTeam?.members || []).map((member: any) => ({
      agentUrl: String(member.agent_url || ''),
      roleName: this.getRoleName(String(member.role_id || '')),
    }));
  }

  submitQuickGoal() {
    if (!this.hub || !this.quickGoalText.trim()) return;
    this.quickGoalBusy = true;
    this.quickGoalResult = null;

    this.hubApi.planGoal(this.hub.url, {
      goal: this.quickGoalText.trim(),
      context: this.quickGoalContext || undefined,
      create_tasks: true
    }).subscribe({
      next: (result: any) => {
        this.completeFirstStartWizard();
        this.quickGoalBusy = false;
        this.quickGoalResult = {
          tasks_created: result?.created_task_ids?.length || 0,
          task_ids: result?.created_task_ids || [],
          goal_id: result?.goal_id
        };
        this.toast.success(`${this.quickGoalResult.tasks_created} Tasks erstellt`);
        this.quickGoalText = '';
        this.quickGoalContext = '';
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

  goToGoal(id: string) {
    this.router.navigate(['/goal', id]);
  }
}

interface GoalModeField {
  name: string;
  label: string;
  type: string;
  options?: string[];
  placeholder?: string;
  default?: unknown;
}

interface GoalModeDefinition {
  id: string;
  title: string;
  description?: string;
  fields?: GoalModeField[];
}

interface GoalWizardStep {
  id: 'goal' | 'context' | 'execution' | 'safety' | 'review';
  title: string;
  helper: string;
}

const DEFAULT_GOAL_PRESETS: DemoPreviewExample[] = [
  {
    id: 'repo-analysis',
    title: 'Repository verstehen',
    goal: 'Analysiere dieses Repository und schlage die wichtigsten naechsten Schritte vor.',
    outcome: 'Hotspots, Risiken und ein kurzer Arbeitsplan.',
    tasks: ['Projektstruktur lesen', 'Architekturgrenzen pruefen', 'Review-Plan erstellen'],
    starter_context: 'Fokus: Einstieg fuer neue Maintainer, Risiken benennen, keine Code-Aenderungen.',
  },
  {
    id: 'bugfix-plan',
    title: 'Bugfix planen',
    goal: 'Untersuche einen Fehlerbericht und plane eine kleine, testbare Korrektur.',
    outcome: 'Reproduktionspfad, Ursache und Regressionstest.',
    tasks: ['Fehler reproduzieren', 'Betroffene Pfade finden', 'Fix und Regressionstest vorschlagen'],
    starter_context: 'Fokus: kleine, testbare Korrektur planen und Regressionen vermeiden.',
  },
  {
    id: 'compose-diagnosis',
    title: 'Start reparieren',
    goal: 'Pruefe Docker- und Compose-Probleme und leite eine robuste lokale Startsequenz ab.',
    outcome: 'Konkrete Startbefehle und naechste Diagnose.',
    tasks: ['Compose-Profile pruefen', 'Ports und Health-Checks auswerten', 'Startpfad dokumentieren'],
    starter_context: 'Fokus: lokaler Start, Compose-Profile, Health-Checks und klare naechste Diagnose.',
  },
];
