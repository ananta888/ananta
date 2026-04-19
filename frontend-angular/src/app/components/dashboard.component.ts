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
  GoalDetail,
  GoalGovernanceSummary,
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
import { OnboardingChecklistComponent } from './onboarding-checklist.component';
import { ControlPlaneFacade } from '../features/control-plane/control-plane.facade';
import { TaskManagementFacade } from '../features/tasks/task-management.facade';
import { UiSkeletonComponent } from './ui-skeleton.component';
import { DashboardAutopilotPanelComponent } from './dashboard-autopilot-panel.component';
import { DashboardTimelinePanelComponent } from './dashboard-timeline-panel.component';
import { DashboardBenchmarkPanelComponent } from './dashboard-benchmark-panel.component';
import { DashboardFacade } from './dashboard.facade';
import { DashboardRefreshRuntimeService } from '../services/dashboard-refresh-runtime.service';

@Component({
  standalone: true,
  selector: 'app-dashboard',
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    OnboardingChecklistComponent,
    UiSkeletonComponent,
    DashboardAutopilotPanelComponent,
    DashboardTimelinePanelComponent,
    DashboardBenchmarkPanelComponent,
  ],
  template: `
    <section class="start-hero">
      <div>
        <h2>Ananta starten</h2>
        <p class="muted">
          Beschreibe ein Ziel, pruefe ein Beispiel oder gehe direkt zu Aufgaben und Ergebnissen.
        </p>
      </div>
      <div class="row gap-sm">
        <button class="primary" (click)="focusQuickGoal()">Ziel eingeben</button>
        <button class="secondary" (click)="loadDemoPreview()">Demo ansehen</button>
      </div>
    </section>

    @if (showFirstStartWizard) {
      <section class="card first-start mb-md" aria-label="Erststart">
        <div class="row space-between">
          <div>
            <h3 class="no-margin">Wie moechtest du starten?</h3>
            <p class="muted mt-sm no-margin">Waehle einen einfachen Einstieg. Du kannst spaeter jederzeit in die tieferen Ansichten wechseln.</p>
          </div>
          <button class="secondary btn-small" (click)="completeFirstStartWizard()">Ausblenden</button>
        </div>
        <div class="grid cols-3 mt-sm">
          <button class="card-light wizard-choice" type="button" (click)="chooseFirstStart('demo')">
            <strong>Demo ansehen</strong>
            <span>Beispiele lesen und bei Bedarf als echte Goals starten.</span>
          </button>
          <button class="card-light wizard-choice" type="button" (click)="chooseFirstStart('goal')">
            <strong>Eigenes Ziel planen</strong>
            <span>Mit einem Satz starten und Tasks erzeugen lassen.</span>
          </button>
          <a class="card-light wizard-choice" [routerLink]="['/board']" (click)="completeFirstStartWizard()">
            <strong>Leer starten</strong>
            <span>Direkt ins Board und Aufgaben manuell anlegen.</span>
          </a>
        </div>
      </section>
    }

    @if (viewState.loading) {
      <app-ui-skeleton [count]="1" [lineCount]="1" lineClass="skeleton block"></app-ui-skeleton>
    }
    @if (viewState.error) {
      <div class="state-banner error">
        <strong>Dashboard konnte nicht geladen werden.</strong>
        <p class="muted no-margin mt-sm">{{ viewState.error }}</p>
        <button class="secondary btn-small mt-sm" (click)="refresh()">Erneut versuchen</button>
      </div>
    }
    @if (!viewState.loading && viewState.empty) {
      <div class="card empty-state">
        <h3>Noch keine Arbeit sichtbar</h3>
        <p class="muted">
          Starte mit einem Ziel oder oeffne die Demo-Beispiele, um typische Ablaeufe kennenzulernen.
        </p>
        <div class="row gap-sm flex-center">
          <button class="primary" (click)="focusQuickGoal()">Ziel eingeben</button>
          <button class="secondary" (click)="loadDemoPreview()">Demo ansehen</button>
          <button class="secondary" [routerLink]="['/board']">Zum Board</button>
        </div>
      </div>
    }

    @if (hub) {
      <div class="start-actions mb-md">
        <a class="card start-action" href="#quick-goal">
          <strong>Ziel planen</strong>
          <span>Ein Satz reicht fuer den ersten Plan.</span>
        </a>
        <button class="card start-action start-action-button" type="button" (click)="loadDemoPreview()">
          <strong>Demo ansehen</strong>
          <span>Beispiele ohne echte Datenmutation.</span>
        </button>
        <a class="card start-action" [routerLink]="['/board']">
          <strong>Aufgaben verfolgen</strong>
          <span>Board, Status und naechste Schritte.</span>
        </a>
        <a class="card start-action" [routerLink]="['/artifacts']">
          <strong>Ergebnisse ansehen</strong>
          <span>Artefakte und Resultate pruefen.</span>
        </a>
      </div>

      @if (demoPreview || demoLoading || demoError) {
        <section class="card mb-md">
          <div class="row space-between">
            <div>
              <h3 class="no-margin">Demo-Vorschau</h3>
              <p class="muted font-sm mt-sm no-margin">
                Beispiele sind read-only und bleiben vom echten Arbeitsmodus getrennt.
              </p>
            </div>
            <button class="secondary btn-small" (click)="demoPreview = null; demoError = ''">Schliessen</button>
          </div>
          @if (demoLoading) {
            <app-ui-skeleton [count]="3" [columns]="3" [lineCount]="3" lineClass="skeleton line"></app-ui-skeleton>
          } @else if (demoError) {
            <div class="state-banner error mt-sm">
              <strong>Demo konnte nicht geladen werden.</strong>
              <p class="muted no-margin mt-sm">{{ demoError }}</p>
              <button class="secondary btn-small mt-sm" (click)="loadDemoPreview()">Erneut versuchen</button>
            </div>
          } @else if (demoPreview?.examples?.length) {
            <div class="grid cols-3 mt-sm">
              @for (example of demoPreview.examples; track example.id) {
                <article class="card-light demo-example">
                  <h4>{{ example.title }}</h4>
                  <p class="muted">{{ example.goal }}</p>
                  <strong>{{ example.outcome }}</strong>
                  <ul>
                    @for (task of example.tasks; track task) {
                      <li>{{ task }}</li>
                    }
                  </ul>
                  <button class="primary btn-small mt-sm" (click)="startDemoExample(example)" [disabled]="quickGoalBusy">
                    Als Goal starten
                  </button>
                </article>
              }
            </div>
          }
        </section>
      }

      <section class="card card-primary mb-md" id="quick-goal">
        <h3 class="no-margin">Ziel planen</h3>
        <p class="muted font-sm mt-sm">Starte einfach mit einem Ziel. Gefuehrte Modi bleiben fuer strukturierte Faelle verfuegbar.</p>

        <div class="preset-strip mt-sm" aria-label="Goal-Vorlagen">
          @for (preset of goalPresets(); track preset.id) {
            <button class="secondary preset-chip" type="button" (click)="applyGoalPreset(preset)" [attr.aria-label]="'Vorlage einsetzen: ' + preset.title">
              {{ preset.title }}
            </button>
          }
        </div>

        <div class="row gap-sm mt-sm flex-end">
          <div class="flex-1">
            <label class="label-no-margin">
              <input
                [(ngModel)]="quickGoalText"
                placeholder="z.B. Analysiere dieses Repository und schlage die naechsten Schritte vor"
                class="w-full"
                aria-label="Quick Goal Beschreibung eingeben"
                #quickGoalInput
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
          <button class="secondary" [routerLink]="['/auto-planner']" aria-label="Zur Auto-Planner Konfiguration navigieren">Mehr Optionen</button>
        </div>
        @if (quickGoalResult) {
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
        }

        <div style="margin: 20px 0; border-top: 1px solid rgba(255,255,255,0.1);"></div>

        <h3 class="no-margin">Gefuehrter Ziel-Assistent</h3>
        <p class="muted font-sm mt-sm">Der Assistent fragt nur die Angaben ab, die dem Hub beim Planen, Zuweisen und Pruefen helfen.</p>

        @if (!selectedGoalMode) {
          <div class="grid cols-4 gap-sm mt-sm">
            @for (mode of goalModes; track mode.id) {
              <div class="card card-light clickable text-center" (click)="setGoalMode(mode)" style="min-height: 100px; display: flex; flex-direction: column; justify-content: center;">
                <div class="mb-xs"><strong>{{ mode.title }}</strong></div>
                <div class="muted font-sm mt-xs">{{ mode.description }}</div>
              </div>
            }
          </div>
        } @else {
          <div class="card card-light mt-sm guided-goal-card">
            <div class="row space-between">
              <div>
                <strong>{{ selectedGoalMode.title }}</strong>
                <p class="muted font-sm no-margin mt-5">{{ activeGoalWizardStep().helper }}</p>
              </div>
              <button class="secondary btn-small" (click)="setGoalMode(null)">Zurueck</button>
            </div>
            <div class="guided-stepper mt-md" aria-label="Schritte der gefuehrten Zielerstellung">
              @for (step of goalWizardSteps; track step.id; let i = $index) {
                <button
                  type="button"
                  class="guided-step"
                  [class.active]="i === goalWizardStepIndex"
                  [class.done]="i < goalWizardStepIndex"
                  (click)="goToGoalWizardStep(i)"
                  [attr.aria-current]="i === goalWizardStepIndex ? 'step' : null"
                >
                  <span>{{ i + 1 }}</span>
                  {{ step.title }}
                </button>
              }
            </div>

            <div class="mt-md">
              @if (activeGoalWizardStep().id === 'goal') {
                <div class="grid gap-sm">
                  @for (field of requiredGoalFields(); track field.name) {
                    <label>
                      {{ field.label }}
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
                      <small class="muted">{{ fieldHelper(field.name) }}</small>
                    </label>
                  }
                </div>
              } @else if (activeGoalWizardStep().id === 'context') {
                <label>
                  Kontext und Eingabedaten
                  <textarea [(ngModel)]="goalModeData['context']" class="w-full" rows="5" placeholder="Links, Dateien, Fehlermeldungen, Repo-Bereich oder wichtige Einschraenkungen"></textarea>
                  <small class="muted">Mehr Kontext reduziert Rueckfragen und hilft dem Hub, Tasks an passende Worker zu geben.</small>
                </label>
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
                <div class="state-banner">
                  <strong>Bereit zum Planen</strong>
                  <p class="muted no-margin mt-sm">
                    Der Hub erstellt daraus planbare Tasks. Worker fuehren die delegierten Schritte aus; Pruefungen und Freigaben bleiben sichtbar.
                  </p>
                </div>
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
            </div>
            <div class="row mt-md space-between">
              <button class="secondary" type="button" (click)="previousGoalWizardStep()" [disabled]="goalWizardStepIndex === 0">Zurueck</button>
              @if (!isLastGoalWizardStep()) {
                <button type="button" (click)="nextGoalWizardStep()" [disabled]="!canContinueGoalWizard()">Weiter</button>
              } @else {
                <button (click)="submitGuidedGoal()" [disabled]="quickGoalBusy || !canContinueGoalWizard()">
                  {{ quickGoalBusy ? 'Plane...' : 'Goal planen' }}
                </button>
              }
            </div>
          </div>
        }
      </section>
    }

    @if (stats) {
      <app-onboarding-checklist />
      <div class="grid cols-3">
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
          <h3>System Status</h3>
          <div class="row gap-sm">
            <div class="status-dot" [class.online]="systemHealth?.status === 'ok'" [class.offline]="systemHealth?.status !== 'ok'" role="status" [attr.aria-label]="'Systemstatus ' + (systemHealth?.status || 'unknown')"></div>
            <strong>{{ systemHealth?.status || ((stats.agents?.online || 0) > 0 ? 'ok' : 'degraded') }}</strong>
          </div>
          <div class="muted font-sm mt-sm">
            Live Sync:
            <strong [class.success]="liveState.systemStreamConnected()" [class.danger]="!liveState.systemStreamConnected()">
              {{ liveState.systemStreamConnected() ? 'connected' : 'idle' }}
            </strong>
          </div>
          <div class="muted font-sm mt-sm">
            Task Snapshot:
            <strong [class.success]="!tasksLoading()" [class.danger]="!!taskCollectionError()">
              {{ tasksLoading() ? 'loading' : 'signal-backed' }}
            </strong>
            @if (tasksLastLoadedAt()) {
              <span> · Stand: {{ ((tasksLastLoadedAt() || 0) * 1000) | date:'HH:mm:ss' }}</span>
            }
          </div>
          @if (liveState.lastSystemEvent()) {
            <div class="muted font-sm mt-sm">
              Letztes Event: <strong>{{ liveState.lastSystemEvent()?.type }}</strong>
            </div>
          }
          @if (systemHealth?.checks?.queue) {
            <div class="muted font-sm mt-sm">
              Queue-Tiefe: <strong>{{ systemHealth.checks.queue.depth || 0 }}</strong>
            </div>
          }
          @if (systemHealth?.checks?.registration?.enabled) {
            <div class="muted font-sm mt-sm">
              Registration: <strong>{{ systemHealth.checks.registration.status }}</strong>
              @if (systemHealth.checks.registration.attempts) {
                <span> · Attempts: {{ systemHealth.checks.registration.attempts }}</span>
              }
            </div>
          }
          @if (systemHealth?.checks?.scheduler) {
            <div class="muted font-sm mt-sm">
              Scheduler: <strong>{{ systemHealth.checks.scheduler.running ? 'running' : 'stopped' }}</strong>
              <span> · Jobs: {{ systemHealth.checks.scheduler.scheduled_count || 0 }}</span>
            </div>
          }
          @if (contracts) {
            <div class="muted font-sm mt-sm">
              Contracts: <strong>{{ contracts.version }}</strong>
              <span> · Schemas: {{ contracts.schema_count || 0 }}</span>
            </div>
            @if (contracts.task_statuses?.canonical_values?.length) {
              <div class="muted status-text-sm mt-sm">
                Task-States: {{ contracts.task_statuses.canonical_values.join(', ') }}
              </div>
            }
          }
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

      <div class="card mt-md">
        <div class="row space-between">
          <div>
            <h3 class="no-margin">Goal Governance & Cost Summary</h3>
            <div class="muted font-sm mt-sm">
              Verifikation, Policy-Entscheidungen und Ausfuehrungskosten des ausgewaehlten Goals.
            </div>
          </div>
          <div class="row gap-sm">
            <select
              aria-label="Goal fuer Governance Summary"
              [(ngModel)]="selectedGoalId"
              (ngModelChange)="refreshGoalReporting($event)"
              [disabled]="goalReportingLoading || !goalsList.length"
            >
              @for (goal of goalsList; track goal.id) {
                <option [value]="goal.id">{{ goal.summary || goal.goal || goal.id }}</option>
              }
            </select>
            <button
              class="secondary"
              (click)="refreshGoalReporting(selectedGoalId)"
              [disabled]="goalReportingLoading"
              aria-label="Goal Governance Summary aktualisieren"
            >
              Refresh
            </button>
          </div>
        </div>
        @if (goalReportingLoading) {
          <app-ui-skeleton [count]="4" [columns]="4" [lineCount]="1" [card]="false" containerClass="mt-sm" lineClass="skeleton line skeleton-40"></app-ui-skeleton>
        } @else if (goalDetail && goalGovernance) {
          <div class="muted font-sm mt-sm">
            Goal:
            <strong>{{ goalDetail?.goal?.summary || goalDetail?.goal?.goal || selectedGoalId }}</strong>
            <span> · Status: {{ goalDetail?.goal?.status || '-' }}</span>
            <span> · Tasks: {{ goalGovernance?.summary?.task_count || goalDetail?.tasks?.length || 0 }}</span>
          </div>
          <div class="grid cols-4 mt-sm">
            <div class="card card-light">
              <div class="muted">Verification</div>
              <strong>{{ goalGovernance?.verification?.passed || 0 }}/{{ goalGovernance?.verification?.total || 0 }}</strong>
              <div class="muted status-text-sm-alt">
                Failed: {{ goalGovernance?.verification?.failed || 0 }} · Escalated: {{ goalGovernance?.verification?.escalated || 0 }}
              </div>
            </div>
            <div class="card card-light">
              <div class="muted">Policy</div>
              <strong>{{ goalGovernance?.policy?.approved || 0 }}</strong>
              <div class="muted status-text-sm-alt">
                Approved · Blocked: {{ goalGovernance?.policy?.blocked || 0 }}
              </div>
            </div>
            <div class="card card-light">
              <div class="muted">Cost Units</div>
              <strong>{{ goalGovernance?.cost_summary?.total_cost_units || 0 | number:'1.2-4' }}</strong>
              <div class="muted status-text-sm-alt">
                Tasks mit Cost: {{ goalGovernance?.cost_summary?.tasks_with_cost || 0 }}
              </div>
            </div>
            <div class="card card-light">
              <div class="muted">Tokens / Latenz</div>
              <strong>{{ goalGovernance?.cost_summary?.total_tokens || 0 }}</strong>
              <div class="muted status-text-sm-alt">
                {{ goalGovernance?.cost_summary?.total_latency_ms || 0 }} ms
              </div>
            </div>
          </div>
          @if (goalCostTasks().length) {
            <div class="table-scroll mt-sm">
              <table class="standard-table table-min-600">
                <thead>
                  <tr class="card-light">
                    <th>Task</th>
                    <th>Status</th>
                    <th>Verification</th>
                    <th>Cost</th>
                    <th>Tokens</th>
                  </tr>
                </thead>
                <tbody>
                  @for (task of goalCostTasks(); track task.id) {
                    <tr>
                      <td>
                        <div><strong>{{ task.title || task.id }}</strong></div>
                        <div class="muted font-sm">{{ task.id }}</div>
                      </td>
                      <td>{{ task.status || '-' }}</td>
                      <td>{{ task.verification_status?.status || '-' }}</td>
                      <td>{{ task.cost_summary?.cost_units || 0 | number:'1.2-4' }}</td>
                      <td>{{ task.cost_summary?.tokens_total || 0 }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          } @else {
            <div class="muted mt-sm">Fuer dieses Goal liegen noch keine taskbezogenen Cost-Summaries vor.</div>
          }
        } @else {
          <div class="muted mt-sm">Noch keine Goals fuer Governance- und Cost-Reporting vorhanden.</div>
        }
      </div>

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
              <div class="muted font-sm mt-sm row space-between">
                <span>Routing: {{ agentRoutingState(agent) }}</span>
                <span>Load: {{ agentCurrentLoad(agent) }}</span>
              </div>
              @if (agent?.liveness) {
                <div class="muted font-sm mt-sm">
                  Last seen: {{ agentLastSeen(agent) }}
                  @if (agent?.liveness?.stale_seconds !== undefined && agent?.liveness?.stale_seconds !== null) {
                    <span> · stale {{ agent.liveness.stale_seconds }}s</span>
                  }
                </div>
              }
              @if (agent?.security_level) {
                <div class="muted font-sm mt-sm">Security: {{ agent.security_level }}</div>
              }
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
  goalsList: GoalListEntry[] = [];
  selectedGoalId = '';
  goalDetail: GoalDetail | null = null;
  goalGovernance: GoalGovernanceSummary | null = null;
  goalReportingLoading = false;
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

  goalPresets(): DemoPreviewExample[] {
    return this.demoPreview?.examples?.length ? this.demoPreview.examples : DEFAULT_GOAL_PRESETS;
  }

  applyGoalPreset(preset: DemoPreviewExample): void {
    this.quickGoalText = preset.goal;
    this.quickGoalContext = preset.starter_context || `Vorlage: ${preset.title}`;
    this.focusQuickGoal();
  }

  chooseFirstStart(choice: 'demo' | 'goal'): void {
    this.completeFirstStartWizard();
    if (choice === 'demo') {
      this.loadDemoPreview();
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
    if (goalId) {
      this.selectedGoalId = goalId;
    }
    this.goalReportingLoading = true;
    this.hubApi.listGoals(this.hub.url).subscribe({
      next: (goals) => {
        this.goalsList = Array.isArray(goals)
          ? [...goals].sort(
              (left: any, right: any) =>
                Number(right?.updated_at || right?.created_at || 0) - Number(left?.updated_at || left?.created_at || 0)
            )
          : [];
        const selectedId = this.resolveGoalReportingId();
        if (!selectedId) {
          this.selectedGoalId = '';
          this.goalDetail = null;
          this.goalGovernance = null;
          this.goalReportingLoading = false;
          return;
        }
        this.selectedGoalId = selectedId;
        let pending = 2;
        const markDone = () => {
          pending -= 1;
          if (pending <= 0) {
            this.goalReportingLoading = false;
          }
        };
        this.hubApi.getGoalDetail(this.hub.url, selectedId).subscribe({
          next: (detail) => {
            this.goalDetail = detail;
            markDone();
          },
          error: () => {
            this.goalDetail = null;
            markDone();
            this.ns.error('Goal-Detail konnte nicht geladen werden');
          }
        });
        this.hubApi.getGoalGovernanceSummary(this.hub.url, selectedId).subscribe({
          next: (summary) => {
            this.goalGovernance = summary;
            markDone();
          },
          error: () => {
            this.goalGovernance = null;
            markDone();
            this.ns.error('Goal-Governance konnte nicht geladen werden');
          }
        });
      },
      error: () => {
        this.goalsList = [];
        this.selectedGoalId = '';
        this.goalDetail = null;
        this.goalGovernance = null;
        this.goalReportingLoading = false;
        this.ns.error('Goals konnten nicht geladen werden');
      }
    });
  }

  agentRoutingState(agent: any): string {
    const available = agent?.liveness?.available_for_routing;
    if (available === false && agent?.status === 'online') return 'paused';
    if (available === true) return 'ready';
    return String(agent?.liveness?.status || agent?.status || 'unknown');
  }

  agentCurrentLoad(agent: any): number {
    return Number(agent?.current_load ?? agent?.routing_signals?.current_load ?? 0);
  }

  agentLastSeen(agent: any): string {
    const lastSeen = Number(agent?.liveness?.last_seen || 0);
    if (!lastSeen) return '—';
    return new Date(lastSeen * 1000).toLocaleTimeString();
  }

  goalCostTasks(): any[] {
    const tasks = Array.isArray(this.goalDetail?.tasks) ? this.goalDetail.tasks : [];
    return [...tasks]
      .filter((task: any) => Number(task?.cost_summary?.cost_units || 0) > 0)
      .sort((left: any, right: any) => Number(right?.cost_summary?.cost_units || 0) - Number(left?.cost_summary?.cost_units || 0))
      .slice(0, 5);
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

  private resolveGoalReportingId(): string {
    if (!this.goalsList.length) {
      return '';
    }
    if (this.selectedGoalId && this.goalsList.some((goal: any) => goal?.id === this.selectedGoalId)) {
      return this.selectedGoalId;
    }
    return String(this.goalsList[0]?.id || '');
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

interface DemoPreviewExample {
  id: string;
  title: string;
  goal: string;
  outcome: string;
  tasks: string[];
  starter_context?: string;
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
