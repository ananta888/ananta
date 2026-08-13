import { Component, HostListener, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { PageIntroComponent } from '../../../shared/ui/layout/page-intro.component';
import { SectionCardComponent } from '../../../shared/ui/layout/section-card.component';
import { SummaryPanelComponent } from '../../../shared/ui/display/summary-panel.component';
import { CaseFlowAgentCanvasComponent } from '../agent-canvas/caseflow-agent-canvas.component';
import { CaseFlowAgentNodeInspectorComponent } from '../agent-canvas/caseflow-agent-node-inspector.component';
import { VisualProcessEditorComponent } from '../../visual-process/visual-process-editor.component';
import { VpEditorStateFacade } from '../../visual-process/vp-editor-state.facade';
import {
  VP_EDITOR_PERSISTENCE,
  VP_EDITOR_STATE,
} from '../../visual-process/vp-editor-state.port';
import {
  CaseFlowStudioView,
  CaseFlowStudioWorkspaceFacade,
} from './caseflow-studio-workspace.facade';

@Component({
  standalone: true,
  selector: 'app-caseflow-studio',
  imports: [
    FormsModule,
    PageIntroComponent,
    RouterLink,
    SectionCardComponent,
    SummaryPanelComponent,
    CaseFlowAgentCanvasComponent,
    CaseFlowAgentNodeInspectorComponent,
    VisualProcessEditorComponent,
  ],
  providers: [
    VpEditorStateFacade,
    CaseFlowStudioWorkspaceFacade,
    { provide: VP_EDITOR_STATE, useExisting: VpEditorStateFacade },
    {
      provide: VP_EDITOR_PERSISTENCE,
      useExisting: CaseFlowStudioWorkspaceFacade,
    },
  ],
  template: `
    <main class="caseflow-page">
      <app-page-intro
        eyebrow="Workflow → Anwendung"
        title="CaseFlow Studio"
        subtitle="Agentenansicht und Prozesseditor bearbeiten denselben lokalen Workflow-Draft."
      />

      <app-section-card
        title="1. Workflow auswählen"
        subtitle="Der Hub bleibt Eigentümer des Prozesses; der Studio-Workspace hält genau einen lokalen Graphzustand."
      >
        <label class="field">
          <span>Gespeicherter Visual Process</span>
          <select
            [ngModel]="workspace.selectedGraphId()"
            (ngModelChange)="workspace.selectGraph($event)"
            [disabled]="workspace.dirty() || workspace.loadingGraph()"
            aria-describedby="graph-switch-status"
          >
            <option value="">Bitte auswählen …</option>
            @if (workspace.selectedGraphIsLocalOnly()) {
              <option [value]="workspace.selectedGraphId()">
                {{ workspace.graph().name }} · lokale Kopie
              </option>
            }
            @for (graph of workspace.graphs(); track graph.id) {
              <option [value]="graph.id">{{ graph.name }}</option>
            }
          </select>
        </label>
        <p id="graph-switch-status" class="muted">
          @if (workspace.dirty()) {
            Workflowwechsel gesperrt: Speichere den aktuellen Draft oder verwirf ihn bewusst im Prozesseditor.
          } @else if (!workspace.graphs().length && !workspace.loadingGraphs()) {
            Noch keine gespeicherten Prozesse vorhanden.
          }
        </p>
        @if (workspace.loadingGraph()) {
          <p role="status">Workflow wird geladen …</p>
        }
        @if (workspace.message()) {
          <p [attr.role]="workspace.hasError() ? 'alert' : 'status'" [class.error]="workspace.hasError()">
            {{ workspace.message() }}
          </p>
        }
        @if (workspace.saveConflict()) {
          <button type="button" class="secondary" (click)="workspace.forkAfterSaveConflict()">
            Als Kopie weiterbearbeiten
          </button>
        }
      </app-section-card>

      @if (workspace.graphLoaded()) {
        <nav class="studio-tabs" role="tablist" aria-label="CaseFlow Workflow-Ansicht">
          <button
            type="button"
            role="tab"
            data-studio-view="agents"
            id="caseflow-agents-tab"
            aria-controls="caseflow-agents-panel"
            [attr.aria-selected]="workspace.view() === 'agents'"
            [attr.tabindex]="workspace.view() === 'agents' ? 0 : -1"
            (click)="selectView('agents')"
            (keydown)="onTabKeydown($event, 'agents')"
          >Agenten</button>
          <button
            type="button"
            role="tab"
            data-studio-view="process"
            id="caseflow-process-tab"
            aria-controls="caseflow-process-panel"
            [attr.aria-selected]="workspace.view() === 'process'"
            [attr.tabindex]="workspace.view() === 'process' ? 0 : -1"
            (click)="selectView('process')"
            (keydown)="onTabKeydown($event, 'process')"
          >Vollständiger Prozess</button>
        </nav>

        @if (workspace.view() === 'agents') {
          <section
            id="caseflow-agents-panel"
            class="studio-panel"
            role="tabpanel"
            aria-labelledby="caseflow-agents-tab"
          >
            <app-section-card
              title="2. Agenten-Canvas"
              subtitle="Die verständliche Ansicht projiziert den kanonischen VisualProcessGraph ohne eigene Persistenz."
              variant="technical"
            >
              <app-caseflow-agent-canvas
                [graph]="workspace.graph()"
                [selectedId]="workspace.selectedId()"
                (graphChange)="workspace.replaceGraphFromAgentView($event)"
                (selectedIdChange)="workspace.selectEntity($event)"
              />
              @if (!workspace.hasAgentSteps()) {
                <div class="empty-state" role="status">
                  <p>Dieser Prozess enthält noch keine rollenfähigen Agent-Schritte.</p>
                  <button type="button" class="secondary" (click)="selectView('process')">
                    Im vollständigen Prozesseditor Rollen konfigurieren
                  </button>
                </div>
              } @else {
                <app-caseflow-agent-node-inspector
                  [graph]="workspace.graph()"
                  [selectedStepId]="workspace.selectedAgentId()"
                  [catalogReadModel]="workspace.catalogReadModel()"
                  (graphChange)="workspace.replaceGraphFromAgentView($event)"
                />
              }
            </app-section-card>
          </section>
        } @else {
          <section
            id="caseflow-process-panel"
            class="studio-panel"
            role="tabpanel"
            aria-labelledby="caseflow-process-tab"
          >
            <app-section-card
              title="2. Workflow entwickeln"
              subtitle="Dieser Editor verwendet denselben ungespeicherten Graph-Draft wie die Agentenansicht."
              variant="technical"
            >
              <app-visual-process-editor
                [graphId]="workspace.graph().id"
                editorMode="full-editor"
              />
            </app-section-card>
          </section>
        }

        <app-section-card
          title="3. Anwendung beschreiben"
          subtitle="Vorschau und Veröffentlichung verwenden den aktuellen In-Memory-Draft; es erfolgt kein verstecktes Neuladen."
        >
          <div class="form-grid">
            <label class="field"><span>Titel</span><input
              [ngModel]="workspace.draft().title"
              (ngModelChange)="workspace.updateDraft({ title: $event })"
            /></label>
            <label class="field"><span>Case-Typ</span><input
              [ngModel]="workspace.draft().caseType"
              (ngModelChange)="workspace.updateDraft({ caseType: $event })"
            /></label>
            <label class="field"><span>Technische ID</span><input
              [ngModel]="workspace.draft().id"
              (ngModelChange)="workspace.updateDraft({ id: $event })"
            /></label>
            <label class="field"><span>Icon-Name</span><input
              [ngModel]="workspace.draft().icon"
              (ngModelChange)="workspace.updateDraft({ icon: $event })"
            /></label>
            <label class="field full"><span>Beschreibung</span><textarea
              rows="3"
              [ngModel]="workspace.draft().description"
              (ngModelChange)="workspace.updateDraft({ description: $event })"
            ></textarea></label>
          </div>
          <div class="actions">
            <button type="button" class="secondary" (click)="workspace.generatePreview()" [disabled]="workspace.busy()">
              UI aus aktuellem Workflow erzeugen
            </button>
            <button type="button" class="primary" (click)="workspace.publish()" [disabled]="workspace.busy() || !workspace.preview()">
              CaseFlow veröffentlichen
            </button>
          </div>
        </app-section-card>
      }

      @if (workspace.preview(); as current) {
        <app-section-card title="Vorschau der generierten Struktur">
          <app-summary-panel
            [title]="current.title"
            [summary]="current.description"
            eyebrow="CaseFlow UI v1"
            [metrics]="[
              { label: 'Seiten', value: current.pages.length },
              { label: 'Komponenten', value: current.pages[0].blocks.length },
              { label: 'Case-Typ', value: current.caseType }
            ]"
          />
          <div class="component-list">
            @for (block of current.pages[0].blocks; track block.id) {
              <span class="badge">{{ block.title }} · {{ block.kind }}</span>
            }
          </div>
          <a [routerLink]="['/caseflow/scenario', current.id]">Anwendungsszenario öffnen</a>
        </app-section-card>
      }
    </main>
  `,
  styles: [`
    .caseflow-page { display: grid; gap: 1rem; padding: 1rem; }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
    .field { display: grid; gap: .35rem; }
    .field.full { grid-column: 1 / -1; }
    .field input, .field select, .field textarea, select { width: 100%; }
    .actions, .component-list, .studio-tabs { display: flex; flex-wrap: wrap; gap: .75rem; }
    .actions, .component-list { margin-top: 1rem; }
    .studio-tabs { border-bottom: 1px solid var(--border-color, #334155); }
    .studio-tabs button { border-radius: .4rem .4rem 0 0; padding: .6rem 1rem; }
    .studio-tabs button[aria-selected='true'] { border-bottom: 3px solid currentColor; font-weight: 700; }
    .studio-tabs button:focus-visible { outline: 3px solid var(--focus-color, #38bdf8); outline-offset: 2px; }
    .studio-panel { min-width: 0; }
    .empty-state { display: grid; justify-items: start; gap: .5rem; padding: 1rem; }
    .muted { min-height: 1.2em; opacity: .75; }
    .error { color: var(--danger-color, #d33); }
    @media (max-width: 48rem) { .form-grid { grid-template-columns: 1fr; } }
  `],
})
export class CaseFlowStudioComponent implements OnInit {
  readonly workspace = inject(CaseFlowStudioWorkspaceFacade);

  ngOnInit(): void {
    this.workspace.connect();
  }

  canLeaveCaseFlowStudio(): boolean {
    if (!this.workspace.dirty()) return true;
    return typeof globalThis.confirm === 'function'
      && globalThis.confirm('Ungespeicherte CaseFlow-Änderungen verwerfen?');
  }

  @HostListener('window:beforeunload', ['$event'])
  preventUnsafeUnload(event: BeforeUnloadEvent): void {
    if (!this.workspace.dirty()) return;
    event.preventDefault();
    event.returnValue = '';
  }

  selectView(view: CaseFlowStudioView): void {
    this.workspace.selectView(view);
  }

  onTabKeydown(event: KeyboardEvent, current: CaseFlowStudioView): void {
    const next = nextViewForKey(event.key, current);
    if (!next) return;
    event.preventDefault();
    const tablist = (event.currentTarget as HTMLElement | null)?.closest('[role="tablist"]');
    this.workspace.selectView(next);
    queueMicrotask(() => {
      tablist?.querySelector<HTMLElement>(`[data-studio-view="${next}"]`)?.focus();
    });
  }
}

function nextViewForKey(
  key: string,
  current: CaseFlowStudioView,
): CaseFlowStudioView | null {
  if (key === 'Home') return 'agents';
  if (key === 'End') return 'process';
  if (key === 'ArrowRight' || key === 'ArrowDown') {
    return current === 'agents' ? 'process' : 'agents';
  }
  if (key === 'ArrowLeft' || key === 'ArrowUp') {
    return current === 'agents' ? 'process' : 'agents';
  }
  return null;
}
