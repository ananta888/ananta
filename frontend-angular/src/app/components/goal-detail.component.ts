import { Component, OnInit, OnDestroy, inject, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { ControlPlaneFacade } from '../features/control-plane/control-plane.facade';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { decisionExplanation, safetyBoundaryExplanation, userFacingTerm } from '../models/user-facing-language';
import { LoadingStateComponent, StatusBadgeComponent, StatusTone } from '../shared/ui/state';
import { MetricCardComponent } from '../shared/ui/display';
import { SectionCardComponent } from '../shared/ui/layout';
import { interval, Subscription } from 'rxjs';

@Component({
  standalone: true,
  selector: 'app-goal-detail',
  imports: [CommonModule, RouterLink, LoadingStateComponent, StatusBadgeComponent, MetricCardComponent, SectionCardComponent],
  template: `
    <div class="container pb-lg">
      @if (loading && !goal) {
        <app-loading-state label="Goal wird geladen" [count]="2" [columns]="2" [lineCount]="5" lineClass="skeleton block"></app-loading-state>
      }

      @if (goal) {
        <div class="goal-header-card card card-primary">
          <div class="row space-between align-start">
            <div>
              <div class="muted font-sm mb-xs">Goal #{{ gid }}</div>
              <h2 class="no-margin">{{ goal.summary || 'Unbenanntes Goal' }}</h2>
            </div>
            <div class="row gap-sm">
              <app-status-badge [label]="goal.status || 'unknown'" [tone]="goalStatusTone()" [dot]="true"></app-status-badge>
              <button class="secondary btn-small" (click)="refresh()">Refresh</button>
            </div>
          </div>
          <div class="goal-description-box mt-md">
            <strong>Beschreibung:</strong>
            <p class="mt-xs">{{ goal.goal }}</p>
          </div>
        </div>

        <app-section-card class="block mt-md result-summary" eyebrow="Abschluss & naechste Schritte" [title]="resultHeadline()" [subtitle]="resultDescription()">
          <button section-actions class="secondary btn-small" [routerLink]="['/board']">Aufgaben oeffnen</button>
          <button section-actions class="secondary btn-small" [routerLink]="['/artifacts']">Ergebnisse oeffnen</button>
          <div class="grid cols-4 gap-sm mt-md">
            <app-metric-card label="Fortschritt" [value]="completedTasks() + '/' + tasks.length" hint="Tasks abgeschlossen"></app-metric-card>
            <app-metric-card label="Offen" [value]="openTasks()" hint="naechste Tasks" tone="warning"></app-metric-card>
            <app-metric-card [label]="term('artifact').label" [value]="artifacts.length" [hint]="term('artifact').technicalLabel + ' / sichtbare Resultate'"></app-metric-card>
            <app-metric-card [label]="term('verification').label" [value]="verificationLabel()" [hint]="term('verification').technicalLabel"></app-metric-card>
          </div>
          <div class="grid cols-2 gap-sm mt-md">
            <div class="state-banner">
              <strong>Warum wird geprueft?</strong>
              <p class="muted no-margin mt-sm">{{ decisionExplanation('verification') }}</p>
            </div>
            <div class="state-banner" [class.warning]="failedTasks() > 0 || openTasks() > 0">
              <strong>Sicherheitsgrenze</strong>
              <p class="muted no-margin mt-sm">{{ resultSafetyExplanation() }}</p>
            </div>
          </div>
          @if (isHintVisible('goal-result')) {
            <div class="state-banner mt-md inline-help">
              <p class="muted no-margin">Nutze diese Seite als Abschlussblick: erst offene Punkte pruefen, dann Ergebnis oeffnen oder Folgeaufgabe starten.</p>
              <button class="secondary btn-small" type="button" (click)="dismissHint('goal-result')">Ausblenden</button>
            </div>
          }
          @if (headlineArtifact()) {
            <div class="state-banner mt-md">
              <strong>{{ headlineArtifact()?.title || 'Wichtigstes Ergebnis' }}</strong>
              <p class="muted no-margin mt-sm">{{ headlineArtifact()?.preview }}</p>
            </div>
          } @else if (!artifacts.length) {
            <div class="state-banner warning mt-md">
              <strong>Noch kein Ergebnisartefakt vorhanden.</strong>
              <p class="muted no-margin mt-sm">Pruefe die offenen Tasks oder starte die Ausfuehrung, damit ein sichtbares Ergebnis entsteht.</p>
            </div>
          }
        </app-section-card>

        <div class="grid cols-3 gap-md mt-md">
          <!-- Linke Spalte: Plan & Status -->
          <div class="col-span-2">
            <div class="card h-full">
              <div class="row space-between">
                <h3 class="no-margin">Plan & Task-Fortschritt</h3>
                <div class="muted font-sm">{{ tasks.length }} Tasks</div>
              </div>

              <div class="task-timeline mt-md">
                @for (task of tasks; track task.id; let i = $index) {
                  <div class="task-node-item" [class.done]="task.status === 'completed'" [class.failed]="task.status === 'failed'" [class.active]="task.status === 'in_progress'">
                    <div class="task-node-status-line">
                      <div class="status-circle"></div>
                      @if (i < tasks.length - 1) { <div class="status-connector"></div> }
                    </div>
                    <div class="task-node-content card card-light clickable" [routerLink]="['/task', task.id]">
                      <div class="row space-between">
                        <strong>{{ task.title }}</strong>
                        <span class="badge font-xs">{{ task.status }}</span>
                      </div>
                      <div class="muted font-sm mt-xs line-clamp-1">{{ task.id }}</div>
                      @if (task.verification_status?.status) {
                        <div class="mt-xs">
                          <span class="badge font-xs" [class.success]="task.verification_status.status === 'passed'">
                            Verification: {{ task.verification_status.status }}
                          </span>
                        </div>
                      }
                    </div>
                  </div>
                }
                @if (!tasks.length) {
                  <div class="empty-state p-lg text-center">
                    <p class="muted">Noch keine Tasks fuer diesen Plan generiert.</p>
                  </div>
                }
              </div>
            </div>
          </div>

          <!-- Rechte Spalte: Artefakte & Kosten -->
          <div class="column flex-column gap-md">
            <div class="card">
              <h3 class="no-margin">Erzeugte Ergebnisse</h3>
              <p class="muted font-sm mt-sm">{{ term('artifact').technicalLabel }} bedeutet hier: {{ term('artifact').hint }}</p>
              <div class="artifact-list mt-md">
                @for (art of artifacts; track art.task_id) {
                  <div class="artifact-item list-item clickable" [routerLink]="['/task', art.task_id]">
                    <div class="font-weight-medium">{{ art.title }}</div>
                    <div class="muted font-sm mt-xs line-clamp-2">{{ art.preview }}</div>
                  </div>
                }
                @if (!artifacts.length) {
                  <p class="muted p-md text-center">Keine Artefakte vorhanden.</p>
                }
              </div>
            </div>

            <div class="card">
              <h3 class="no-margin">Governance & Kosten</h3>
              <p class="muted font-sm mt-sm">Governance fasst Freigaben, Pruefung und Kosten nachvollziehbar zusammen.</p>
              @if (costSummary) {
                <div class="grid cols-2 gap-sm mt-md">
                   <div>
                     <div class="muted font-sm">Tokens</div>
                     <strong>{{ costSummary.total_tokens | number }}</strong>
                   </div>
                   <div>
                     <div class="muted font-sm">Kosten (Units)</div>
                     <strong>{{ costSummary.total_cost_units | number:'1.2-4' }}</strong>
                   </div>
                </div>
              }
              @if (governance) {
                <div class="mt-md">
                   <div class="row space-between font-sm">
                     <span>{{ term('verification').label }}:</span>
                     <strong>{{ governance.verification?.passed }}/{{ governance.verification?.total }} passed</strong>
                   </div>
                   <div class="row space-between font-sm mt-xs">
                     <span>Policies:</span>
                     <strong>{{ governance.policy?.approved }} approved</strong>
                   </div>
                </div>
              }
            </div>
          </div>
        </div>
      }
    </div>

    <style>
      .task-timeline {
        display: flex;
        flex-direction: column;
      }
      .task-node-item {
        display: flex;
        gap: 15px;
        margin-bottom: 10px;
      }
      .task-node-status-line {
        display: flex;
        flex-direction: column;
        align-items: center;
        width: 20px;
        padding-top: 15px;
      }
      .status-circle {
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: var(--border);
        border: 2px solid var(--card-bg);
        z-index: 2;
      }
      .status-connector {
        width: 2px;
        flex-grow: 1;
        background: var(--border);
        margin: 5px 0;
      }
      .task-node-content {
        flex-grow: 1;
        padding: 12px;
        margin: 0 !important;
      }
      .task-node-item.done .status-circle, .task-node-item.done .status-connector {
        background: var(--success);
      }
      .task-node-item.active .status-circle {
        background: var(--warning);
        box-shadow: 0 0 0 4px rgba(255, 193, 7, 0.2);
      }
      .task-node-item.failed .status-circle {
        background: var(--danger);
      }
      .line-clamp-1 { display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden; }
      .line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
      .artifact-item { padding: 10px; border-radius: 4px; }
      .artifact-item:hover { background: rgba(255,255,255,0.05); }
      .result-summary h3 {
        max-width: 70ch;
      }
    </style>
  `
})
export class GoalDetailComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private facade = inject(ControlPlaneFacade);
  private dir = inject(AgentDirectoryService);
  private ns = inject(NotificationService);
  private cdr = inject(ChangeDetectorRef);

  gid = '';
  goal: any;
  tasks: any[] = [];
  artifacts: any[] = [];
  artifactSummary: any;
  governance: any;
  costSummary: any;
  loading = false;
  hiddenHints = new Set<string>((localStorage.getItem('ananta.hidden-hints') || '').split(',').filter(Boolean));
  private sub?: Subscription;

  get hub() {
    return this.dir.list().find(a => a.role === 'hub');
  }

  ngOnInit() {
    this.route.paramMap.subscribe(params => {
      this.gid = params.get('id') || '';
      this.refresh();
    });
    this.sub = interval(15000).subscribe(() => this.refresh(true));
  }

  ngOnDestroy() {
    this.sub?.unsubscribe();
  }

  refresh(silent = false) {
    if (!this.hub || !this.gid) return;
    if (!silent) this.loading = true;

    this.facade.getGoalDetail(this.hub.url, this.gid).subscribe({
      next: (res: any) => {
        this.goal = res.goal;
        this.tasks = res.tasks || [];
        this.artifacts = res.artifacts?.artifacts || [];
        this.artifactSummary = res.artifacts || null;
        this.governance = res.governance;
        this.costSummary = res.cost_summary;
        this.loading = false;
        this.cdr.detectChanges();
      },
      error: () => {
        this.loading = false;
        if (!silent) this.ns.error('Goal-Details konnten nicht geladen werden.');
        this.cdr.detectChanges();
      }
    });
  }

  completedTasks(): number {
    return this.tasks.filter(task => task?.status === 'completed').length;
  }

  failedTasks(): number {
    return this.tasks.filter(task => task?.status === 'failed').length;
  }

  openTasks(): number {
    return Math.max(0, this.tasks.length - this.completedTasks() - this.failedTasks());
  }

  headlineArtifact(): any | null {
    return this.artifactSummary?.headline_artifact || this.artifacts[0] || null;
  }

  verificationLabel(): string {
    const passed = Number(this.governance?.verification?.passed || 0);
    const total = Number(this.governance?.verification?.total || 0);
    if (!total) return 'offen';
    return `${passed}/${total}`;
  }

  resultHeadline(): string {
    if (this.goal?.status === 'completed' || (this.tasks.length > 0 && this.completedTasks() === this.tasks.length)) {
      return 'Goal abgeschlossen';
    }
    if (this.failedTasks() > 0) {
      return 'Goal braucht Aufmerksamkeit';
    }
    if (this.openTasks() > 0) {
      return 'Goal ist in Arbeit';
    }
    return 'Goal ist vorbereitet';
  }

  resultDescription(): string {
    if (this.failedTasks() > 0) {
      return 'Einige Tasks sind fehlgeschlagen. Oeffne die betroffenen Aufgaben, pruefe Logs und starte gezielt nach.';
    }
    if (this.openTasks() > 0) {
      return 'Die naechsten Schritte sind im Task-Plan sichtbar. Oeffne das Board, um Fortschritt und Blocker zu pruefen.';
    }
    if (this.artifacts.length > 0 || this.headlineArtifact()) {
      return 'Die wichtigsten Ergebnisse sind unten zusammengefasst und als Artefakte erreichbar.';
    }
    return 'Noch fehlen sichtbare Ergebnisse. Starte oder pruefe die erzeugten Tasks, um ein Abschlussartefakt zu erhalten.';
  }

  resultSafetyExplanation(): string {
    if (this.failedTasks() > 0) return safetyBoundaryExplanation('failed');
    if (this.openTasks() > 0) return 'Noch sind nicht alle Aufgaben fertig. Ergebnisse koennen sich aendern, bis offene Tasks abgeschlossen oder bewusst verworfen sind.';
    if (this.verificationLabel() === 'offen') return safetyBoundaryExplanation('verification');
    return 'Die sichtbaren Ergebnisse haben die bekannten Pruefschritte durchlaufen oder enthalten keine offenen Warnungen.';
  }

  goalStatusTone(): StatusTone {
    const status = String(this.goal?.status || '').toLowerCase();
    if (status === 'completed') return 'success';
    if (status === 'failed') return 'error';
    if (status === 'planning' || status === 'planned' || status === 'running' || status === 'in_progress') return 'warning';
    return 'unknown';
  }

  isHintVisible(key: string): boolean {
    return !this.hiddenHints.has(key);
  }

  dismissHint(key: string): void {
    this.hiddenHints.add(key);
    localStorage.setItem('ananta.hidden-hints', Array.from(this.hiddenHints).join(','));
  }

  term = userFacingTerm;
  decisionExplanation = decisionExplanation;
}
