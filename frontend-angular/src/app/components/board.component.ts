import { Component, OnDestroy, OnInit, inject } from '@angular/core';

import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { DragDropModule, CdkDragDrop } from '@angular/cdk/drag-drop';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { normalizeTaskStatus } from '../utils/task-status';
import { TaskStatusDisplayPipe } from '../pipes/task-status-display.pipe';
import { UiSkeletonComponent } from './ui-skeleton.component';
import { TaskManagementFacade } from '../features/tasks/task-management.facade';
import { decisionExplanation, safetyBoundaryExplanation, userFacingTerm } from '../models/user-facing-language';
import { EmptyStateComponent } from '../shared/ui/state';
import { DecisionExplanationComponent, NextStepsComponent, NextStepAction } from '../shared/ui/display';

@Component({
  standalone: true,
  selector: 'app-board',
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    DragDropModule,
    TaskStatusDisplayPipe,
    UiSkeletonComponent,
    EmptyStateComponent,
    DecisionExplanationComponent,
    NextStepsComponent,
  ],
  template: `
    <div class="row flex-between">
      <h2>Board</h2>
      <div class="row board-toolbar">
        <input class="board-search" [(ngModel)]="searchText" (ngModelChange)="persistBoardPrefs()" placeholder="Suchen..." aria-label="Tasks durchsuchen">
        <div class="button-group">
          <button (click)="setView('board')" [class.secondary]="view !== 'board'" aria-label="Sprint Board Ansicht" [attr.aria-pressed]="view === 'board'">Sprint Board</button>
          <button (click)="setView('scrum')" [class.secondary]="view !== 'scrum'" aria-label="Scrum Insights Ansicht" [attr.aria-pressed]="view === 'scrum'">Scrum Insights</button>
        </div>
        <button (click)="reload()" class="button-outline" aria-label="Board aktualisieren">Aktualisieren</button>
      </div>
    </div>
    @if (hub && lastLoadedAt()) {
      <div class="muted font-sm mb-sm">Live Snapshot: {{ lastLoadedAt()! * 1000 | date:'HH:mm:ss' }}</div>
    }
    @if (!hub) {
      <p class="muted">Kein Hub-Agent konfiguriert.</p>
    }

    @if (hub && view === 'board') {
      <div>
        @if (isHintVisible('board-routing')) {
          <div class="state-banner mb-md inline-help">
            <div>
              <strong>Wie das Board entscheidet</strong>
              <p class="muted no-margin mt-sm">{{ decisionExplanation('routing') }} Blockierte Aufgaben bleiben sichtbar, weil Ananta dort bewusst auf Klaerung wartet.</p>
            </div>
            <button class="secondary btn-small" type="button" (click)="dismissHint('board-routing')">Ausblenden</button>
          </div>
        }
        <div class="card row gap-sm flex-end">
          <label for="new-task-input">Neuer Task
            <input id="new-task-input" [(ngModel)]="newTitle" placeholder="Task-Titel" aria-required="true" />
          </label>
          <button (click)="create()" [disabled]="!newTitle" data-test="btn-create-task" aria-label="Neuen Task anlegen">Anlegen</button>
          @if (err) {
            <span class="danger" role="alert">{{err}}</span>
          }
        </div>
        @if (!loading && tasks.length === 0) {
          <app-empty-state
            class="block mb-lg"
            title="Noch keine Aufgaben im Board"
            description="Lege oben eine einzelne Aufgabe an oder starte auf dem Dashboard mit einem Ziel, damit Ananta daraus passende Tasks ableitet."
            primaryLabel="Ziel planen"
            [primaryRouterLink]="['/dashboard']"
            secondaryLabel="Beispiel einsetzen"
            (secondary)="newTitle = 'Repository analysieren und naechste Schritte vorschlagen'"
          ></app-empty-state>
        }
        @if (loading) {
          <app-ui-skeleton
            [count]="boardColumns.length"
            [columns]="2"
            [lineCount]="4"
            containerClass="board-column"
            lineClass="skeleton line skeleton-line-40">
          </app-ui-skeleton>
        }
        @if (!loading) {
          <div class="grid cols-2 board-grid">
            @for (col of boardColumns; track col) {
              <div class="card board-column"
                cdkDropList
                [id]="col.id"
                [cdkDropListData]="tasksBy(col.id)"
                [cdkDropListConnectedTo]="dropListIds"
                (cdkDropListDropped)="onDrop($event, col.id)"
                role="region"
                [attr.aria-label]="col.label + ' Spalte'">
                <h3>{{col.label}}</h3>
                <div class="board-dropzone">
                  @for (t of tasksBy(col.id); track t) {
                    <div
                      class="board-item row"
                      cdkDrag
                      [cdkDragData]="t"
                      role="button"
                      tabindex="0"
                      [attr.aria-label]="'Task: ' + t.title">
                      <a [routerLink]="['/task', t.id]" [attr.aria-label]="'Task Details für ' + t.title">{{t.title}}</a>
                      @if (t.priority) {
                        <span class="muted font-sm">{{t.priority}}</span>
                      }
                    </div>
                  }
                  @if (!tasksBy(col.id).length) {
                    <div class="muted board-empty">{{ emptyColumnHint(col.id) }}</div>
                  }
                </div>
              </div>
            }
          </div>
        }
      </div>
    }

    @if (hub && view === 'scrum') {
      <div>
        <div class="grid cols-2">
          <div class="card">
            <h3>Burndown Chart</h3>
            <div class="burndown-chart">
              <svg width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Burndown Chart zeigt Fortschritt von Tasks über Zeit">
                <line x1="0" y1="0" x2="100" y2="100" stroke="gray" stroke-dasharray="2" />
                <polyline [attr.points]="'0,0 20,15 40,40 60,45 80,70 100,' + getBurndownValue()" fill="none" stroke="red" stroke-width="2" />
              </svg>
              <div class="burndown-chart-legend">
                <span>Start</span><span>Mitte</span><span>Ende</span>
              </div>
            </div>
            <p class="muted font-sm text-center">Done: {{tasksBy('completed').length}} / Total: {{tasks.length}}</p>
          </div>
          <div class="card">
            <h3>Roadmap</h3>
            <div class="muted font-sm mb-sm">Blocked: {{ tasksBy('blocked').length }} | In Progress: {{ tasksBy('in_progress').length }}</div>
            @if (tasksBy('blocked').length) {
              <div class="state-banner warning mb-sm">
                <strong>{{ term('blocked').label }}</strong>
                <p class="muted no-margin mt-sm">{{ safetyBoundaryExplanation('blocked') }}</p>
                <app-decision-explanation class="block mt-sm" kind="blocked"></app-decision-explanation>
                <app-next-steps class="block mt-sm" [steps]="blockedNextSteps()" title="Naechste Schritte"></app-next-steps>
              </div>
            }
            @for (t of getRoadmapTasks(); track t) {
              <div class="roadmap-task">
                <strong>{{t.title}}</strong>
                <div class="muted font-sm">{{t.description?.substring(0, 100)}}...</div>
                <div class="mt-sm">
                  <span class="tag" [class.tag-success]="normalizeTaskStatus(t.status) === 'completed'" [class.tag-warning]="normalizeTaskStatus(t.status) !== 'completed'">{{t.status | taskStatusDisplay}}</span>
                </div>
              </div>
            }
            @if (getRoadmapTasks().length === 0) {
              <app-empty-state
                title="Keine Roadmap-Aufgaben gefunden"
                description="Sobald Tasks mit To-Do-Status oder Roadmap-Bezug vorhanden sind, erscheinen sie hier."
                [compact]="true"
              ></app-empty-state>
            }
          </div>
        </div>
      </div>
    }
  `
})
export class BoardComponent implements OnInit, OnDestroy {
  private dir = inject(AgentDirectoryService);
  private ns = inject(NotificationService);
  private taskFacade = inject(TaskManagementFacade);

  hub = this.dir.list().find(a => a.role === 'hub');
  newTitle = '';
  searchText = localStorage.getItem('ananta.board.search') || '';
  err = '';
  view: 'board' | 'scrum' = (localStorage.getItem('ananta.board.view') as 'board' | 'scrum') || 'board';
  hiddenHints = new Set<string>((localStorage.getItem('ananta.hidden-hints') || '').split(',').filter(Boolean));
  boardColumns = [
    { id: 'todo', label: 'To-Do' },
    { id: 'in_progress', label: 'In-Progress' },
    { id: 'blocked', label: 'Blocked' },
    { id: 'completed', label: 'Done' }
  ];
  dropListIds = this.boardColumns.map(col => col.id);

  ngOnInit() {
    if (this.hub?.url) {
      this.taskFacade.connectTaskCollection(this.hub.url);
    }
  }

  ngOnDestroy() {
    this.taskFacade.disconnectTaskCollection(this.hub?.url);
  }

  get tasks(): any[] {
    return this.taskFacade.tasks();
  }

  get loading(): boolean {
    return this.taskFacade.tasksLoading();
  }

  lastLoadedAt() {
    return this.taskFacade.tasksLastLoadedAt();
  }

  reload(){
    if(!this.hub) return;
    this.taskFacade.reloadTaskCollection();
  }
  normalizeTaskStatus = normalizeTaskStatus;
  blockedNextSteps(): NextStepAction[] {
    return [
      { id: 'open-timeline', label: 'Timeline oeffnen', description: 'Guardrails und Blockierungsgruende ansehen.', routerLink: ['/timeline'] },
      { id: 'open-dashboard', label: 'Dashboard oeffnen', description: 'Goal neu planen oder Kontext nachreichen.', routerLink: ['/dashboard'] },
      { id: 'open-settings', label: 'Config pruefen', description: 'Profile und Policies einsehen.', routerLink: ['/settings'] },
    ];
  }

  persistBoardPrefs() {
    localStorage.setItem('ananta.board.search', this.searchText || '');
    localStorage.setItem('ananta.board.view', this.view);
  }

  setView(view: 'board' | 'scrum') {
    this.view = view;
    this.persistBoardPrefs();
  }

  tasksBy(status: string) {
    if (!Array.isArray(this.tasks)) return [];
    return this.tasks.filter((t: any) => {
      const normalized = this.normalizeTaskStatus(t.status);
      const desired = this.normalizeTaskStatus(status);
      const matchStatus = normalized === desired;
      const matchSearch = !this.searchText ||
        (t.title || '').toLowerCase().includes(this.searchText.toLowerCase()) ||
        (t.description || '').toLowerCase().includes(this.searchText.toLowerCase());
      return matchStatus && matchSearch;
    });
  }

  getBurndownValue() {
    const total = this.tasks.length || 1;
    const done = this.tasksBy('completed').length;
    return 100 - (done / total * 100);
  }

  getRoadmapTasks() {
    return this.tasks.filter(t => (t.title||'').toLowerCase().includes('roadmap') || this.normalizeTaskStatus(t.status) === 'todo');
  }

  onDrop(event: CdkDragDrop<any[]>, newStatus: string) {
    const task = event.item?.data;
    if (!this.hub || !task) return;
    const current = this.normalizeTaskStatus(task.status);
    const desired = this.normalizeTaskStatus(newStatus);
    if (current === desired) return;
    const previousStatus = task.status;
    task.status = desired;
    this.taskFacade.patchTask(this.hub.url, task.id, { status: desired }).subscribe({
      next: () => this.ns.success(`Status auf ${desired} aktualisiert`),
      error: () => {
        task.status = previousStatus;
        this.ns.error('Status-Update fehlgeschlagen');
      },
      complete: () => this.taskFacade.reloadTaskCollection(),
    });
  }

  create(){
    if(!this.hub || !this.newTitle) return;
    this.taskFacade.createTask(this.hub.url, { title: this.newTitle, status: 'todo' }).subscribe({
      next: () => { this.newTitle = ''; this.err = ''; this.reload(); },
      error: () => { this.err = 'Fehler beim Anlegen'; }
    });
  }

  term = userFacingTerm;
  decisionExplanation = decisionExplanation;
  safetyBoundaryExplanation = safetyBoundaryExplanation;

  emptyColumnHint(status: string): string {
    const normalized = this.normalizeTaskStatus(status);
    if (normalized === 'blocked') return 'Keine Aufgaben warten auf Klaerung.';
    if (normalized === 'completed') return 'Noch keine Aufgaben abgeschlossen.';
    if (normalized === 'in_progress') return 'Gerade keine aktive Bearbeitung.';
    return 'Keine offenen Aufgaben.';
  }

  isHintVisible(key: string): boolean {
    return !this.hiddenHints.has(key);
  }

  dismissHint(key: string): void {
    this.hiddenHints.add(key);
    localStorage.setItem('ananta.hidden-hints', Array.from(this.hiddenHints).join(','));
  }
}
