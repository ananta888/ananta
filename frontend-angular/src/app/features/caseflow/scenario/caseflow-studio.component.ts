import {
  Component,
  HostListener,
  OnDestroy,
  OnInit,
  computed,
  effect,
  inject,
  untracked,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { PageIntroComponent } from '../../../shared/ui/layout/page-intro.component';
import { SectionCardComponent } from '../../../shared/ui/layout/section-card.component';
import { SummaryPanelComponent } from '../../../shared/ui/display/summary-panel.component';
import { CaseFlowAgentCanvasComponent } from '../agent-canvas/caseflow-agent-canvas.component';
import { CaseFlowAgentEdgeInspectorComponent } from '../agent-canvas/caseflow-agent-edge-inspector.component';
import { CaseFlowAgentNodeInspectorComponent } from '../agent-canvas/caseflow-agent-node-inspector.component';
import { CaseFlowAgentNodeRuntimeInspectorComponent } from '../agent-canvas/caseflow-agent-node-runtime-inspector.component';
import { CaseFlowAgentRuntimeSessionFacade } from '../agent-canvas/caseflow-agent-runtime-session.facade';
import type { CaseFlowEdgeIdentity } from '../agent-canvas/caseflow-edge-trace.models';
import { VisualProcessEditorComponent } from '../../visual-process/visual-process-editor.component';
import { normalizeVpDefinitionHash } from '../../visual-process/vp-definition-hash';
import { VpEditorStateFacade } from '../../visual-process/vp-editor-state.facade';
import {
  VP_EDITOR_PERSISTENCE,
  VP_EDITOR_STATE,
} from '../../visual-process/vp-editor-state.port';
import {
  CaseFlowStudioView,
  CaseFlowStudioWorkspaceFacade,
} from './caseflow-studio-workspace.facade';
import { CaseFlowStudioSelectionFacade } from './caseflow-studio-selection.facade';

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
    CaseFlowAgentEdgeInspectorComponent,
    CaseFlowAgentNodeInspectorComponent,
    CaseFlowAgentNodeRuntimeInspectorComponent,
    VisualProcessEditorComponent,
  ],
  providers: [
    VpEditorStateFacade,
    CaseFlowStudioWorkspaceFacade,
    CaseFlowAgentRuntimeSessionFacade,
    CaseFlowStudioSelectionFacade,
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
            [disabled]="workspace.dirty() || workspace.loadingGraph() || workspace.presetBusy()"
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

        <section
          class="runtime-session-status"
          data-caseflow-runtime-status
          [attr.data-state]="runtime.state()"
          [attr.data-evidence-suppressed]="runtimeEvidenceSuppressed() ? 'true' : null"
          [attr.role]="runtime.state() === 'access_revoked' || runtime.state() === 'error' ? 'alert' : 'status'"
        >
          <div>
            <strong>Hub-Runtime</strong>
            <p>{{ runtimeStatusLabel() }}</p>
            @if (runtime.runId(); as runId) {
              <p class="runtime-scope">
                Workflow <code>{{ runtime.workflowId() }}</code> · Run <code>{{ runId }}</code>
              </p>
            }
            @if (runtime.errorCode(); as errorCode) {
              <small>{{ errorCode }}</small>
            }
            @if (runtimeEvidenceSuppressed()) {
              <small data-runtime-evidence-suppressed>
                Der lokale Draft weicht vom gebundenen Run ab. Runtime und Trace bleiben bis zur erneuten autoritativen Bindung ausgeblendet.
              </small>
            }
          </div>
          <button
            type="button"
            class="secondary"
            data-refresh-caseflow-runtime
            (click)="runtime.refresh()"
            [disabled]="!runtimeRefreshAvailable()"
          >Runtime aktualisieren</button>
        </section>

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
              <div class="preset-tool" aria-labelledby="gauntlet-preset-title">
                <div>
                  <strong id="gauntlet-preset-title">Builder/Critic Gauntlet</strong>
                  <p class="muted">
                    Fügt Lead, Builder, Critic und die begrenzte Feedback-Schleife ein. Die Critic-Kontextquelle wird frisch gegen den Hub-Katalog geprüft.
                  </p>
                </div>
                <label class="field">
                  <span>Autorisierte Benchmark-Kontextquelle</span>
                  <select
                    data-gauntlet-context-source
                    [ngModel]="workspace.gauntletContextSourceId()"
                    (ngModelChange)="workspace.selectGauntletContextSource($event)"
                    [disabled]="workspace.presetBusy() || workspace.busy() || !workspace.gauntletContextSourceIds().length"
                  >
                    <option value="">Bitte auswählen …</option>
                    @for (contextSourceId of workspace.gauntletContextSourceIds(); track contextSourceId) {
                      <option [value]="contextSourceId">{{ contextSourceId }}</option>
                    }
                  </select>
                </label>
                <button
                  type="button"
                  class="secondary"
                  data-apply-gauntlet-preset
                  (click)="workspace.applyBuilderCriticGauntlet()"
                  [disabled]="!workspace.canApplyBuilderCriticGauntlet()"
                >
                  @if (workspace.presetBusy()) { Preset wird geprüft … } @else { Gauntlet einfügen }
                </button>
                @if (!workspace.gauntletContextSourceIds().length) {
                  <p class="muted" role="status">
                    Keine aktuell autorisierte Kontextquelle verfügbar.
                  </p>
                }
              </div>
              <app-caseflow-agent-canvas
                [graph]="workspace.graph()"
                [runtimeOverlay]="visibleRuntimeOverlay()"
                [edgeTraceReadModel]="visibleEdgeTraceReadModel()"
                [selectedNodeId]="selection.selectedNodeId()"
                [selectedEdgeIdentity]="selection.selectedEdge()"
                (graphChange)="workspace.replaceGraphFromAgentView($event)"
                (nodeSelected)="selectNode($event)"
                (edgeIdentitySelected)="selectEdge($event)"
              />
              @if (!workspace.hasAgentSteps()) {
                <div class="empty-state" role="status">
                  <p>Dieser Prozess enthält noch keine rollenfähigen Agent-Schritte.</p>
                  <button type="button" class="secondary" (click)="selectView('process')">
                    Im vollständigen Prozesseditor Rollen konfigurieren
                  </button>
                </div>
              } @else if (selection.selectedNodeId(); as selectedNodeId) {
                <div class="agent-inspector-grid" data-selected-kind="node">
                  <app-caseflow-agent-node-inspector
                    [graph]="workspace.graph()"
                    [selectedStepId]="selectedNodeId"
                    [catalogReadModel]="workspace.catalogReadModel()"
                    (graphChange)="workspace.replaceGraphFromAgentView($event)"
                  />
                  <app-caseflow-agent-node-runtime-inspector
                    [graph]="workspace.graph()"
                    [selectedStepId]="selectedNodeId"
                    [workflowId]="runtime.workflowId() ?? ''"
                    [runId]="runtime.runId() ?? ''"
                    [runtimeOverlay]="visibleRuntimeOverlay()"
                    [traceReadModel]="visibleEdgeTraceReadModel()"
                    [traceReadModelReason]="runtimeEvidenceReason()"
                    (accessRevoked)="revokeRuntimeAccess($event)"
                  />
                </div>
              } @else if (selection.selectedEdge(); as selectedEdge) {
                <div data-selected-kind="edge">
                  <app-caseflow-agent-edge-inspector
                    [workflowId]="runtime.workflowId() ?? ''"
                    [runId]="runtime.runId() ?? ''"
                    [edge]="selectedEdge"
                    [reverseEdge]="selection.reverseEdge()"
                    [traceReadModel]="visibleEdgeTraceReadModel()"
                    (directionSelected)="selectEdge($event)"
                  />
                </div>
              } @else {
                <p class="empty-state" role="status">
                  Wähle einen Agenten für Konfiguration, Runtime und Trace oder eine gerichtete Verbindung für Kommunikation und Telemetrie.
                </p>
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
              <div class="process-runtime-affordance">
                <p>{{ runtimeStatusLabel() }}</p>
                <button
                  type="button"
                  class="secondary"
                  data-show-agent-runtime
                  (click)="selectView('agents')"
                >Agenten-Runtime und Trace öffnen</button>
              </div>
              <app-visual-process-editor
                [graphId]="workspace.graph().id"
                editorMode="full-editor"
                runtimeMode="external-readonly"
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
            <button type="button" class="primary" (click)="workspace.publish()" [disabled]="workspace.busy() || workspace.presetBusy() || !workspace.preview()">
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
    .runtime-session-status, .process-runtime-affordance {
      display: flex; align-items: center; justify-content: space-between; gap: 1rem;
      padding: .75rem 1rem; border: 1px solid var(--border-color, #334155); border-radius: .5rem;
    }
    .runtime-session-status p, .process-runtime-affordance p { margin: .25rem 0 0; }
    .runtime-session-status[data-state='access_revoked'], .runtime-session-status[data-state='error'] {
      border-color: var(--danger-color, #d33);
    }
    .runtime-scope { overflow-wrap: anywhere; }
    .agent-inspector-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
    .process-runtime-affordance { margin-bottom: 1rem; }
    .preset-tool { display: grid; gap: .75rem; margin-bottom: 1rem; padding: 1rem; border: 1px solid var(--border-color, #334155); border-radius: .5rem; }
    .preset-tool > button { justify-self: start; }
    .empty-state { display: grid; justify-items: start; gap: .5rem; padding: 1rem; }
    .muted { min-height: 1.2em; opacity: .75; }
    .error { color: var(--danger-color, #d33); }
    @media (max-width: 48rem) {
      .form-grid, .agent-inspector-grid { grid-template-columns: 1fr; }
      .runtime-session-status, .process-runtime-affordance { align-items: stretch; flex-direction: column; }
    }
  `],
})
export class CaseFlowStudioComponent implements OnInit, OnDestroy {
  readonly workspace = inject(CaseFlowStudioWorkspaceFacade);
  readonly runtime = inject(CaseFlowAgentRuntimeSessionFacade);
  readonly selection = inject(CaseFlowStudioSelectionFacade);
  readonly runtimeEvidenceSuppressed = computed(() => {
    if (this.workspace.dirty()) return true;
    const overlay = this.runtime.runtimeOverlay();
    if (overlay === null) return false;
    const graphHash = normalizeVpDefinitionHash(this.workspace.graph().base_graph_hash);
    const snapshotHash = normalizeVpDefinitionHash(overlay.snapshot_hash);
    return graphHash === null || snapshotHash === null || graphHash !== snapshotHash;
  });
  readonly visibleRuntimeOverlay = computed(() => (
    this.runtimeEvidenceSuppressed() ? null : this.runtime.runtimeOverlay()
  ));
  readonly visibleEdgeTraceReadModel = computed(() => (
    this.runtimeEvidenceSuppressed() ? null : this.runtime.edgeTraceReadModel()
  ));
  readonly runtimeEvidenceReason = computed(() => (
    this.runtimeEvidenceSuppressed()
      ? this.workspace.dirty()
        ? 'caseflow_runtime_draft_changed'
        : 'caseflow_runtime_snapshot_mismatch'
      : this.runtime.errorCode()
  ));

  private readonly runtimeGraphId = computed(() => {
    if (!this.workspace.graphLoaded()) return null;
    const graphId = this.workspace.graph().id;
    return graphId && graphId === this.workspace.selectedGraphId() ? graphId : null;
  });
  private attachedRuntimeGraphId: string | null = null;

  private readonly runtimeAttachmentEffect = effect(() => {
    const graphId = this.runtimeGraphId();
    if (graphId === this.attachedRuntimeGraphId) return;
    if (this.attachedRuntimeGraphId !== null) this.runtime.detach();
    this.selection.clear();
    this.attachedRuntimeGraphId = graphId;
    if (graphId !== null) {
      this.runtime.attach({ graph_id: graphId, workflow_id: graphId });
    }
  });

  private readonly selectionReconciliationEffect = effect(() => {
    if (!this.workspace.graphLoaded()) {
      this.selection.clear();
      return;
    }
    const graph = this.workspace.graph();
    if (graph.id !== this.workspace.selectedGraphId()) {
      this.selection.clear();
      return;
    }
    untracked(() => {
      const previous = this.selection.selection();
      this.selection.reconcileGraph(graph);
      if (previous?.kind === 'node'
        && this.selection.selection() === null
        && this.workspace.selectedId() === previous.step_id) {
        this.workspace.selectEntity(null);
      }
    });
  });

  ngOnInit(): void {
    this.workspace.connect();
  }

  ngOnDestroy(): void {
    this.runtime.detach();
    this.selection.clear();
    this.attachedRuntimeGraphId = null;
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

  selectNode(stepId: string): void {
    this.selection.selectNode(this.workspace.graph(), stepId);
    this.workspace.selectEntity(this.selection.selectedNodeId());
  }

  selectEdge(edge: Readonly<CaseFlowEdgeIdentity>): void {
    this.selection.selectEdge(this.workspace.graph(), edge);
    this.workspace.selectEntity(null);
  }

  revokeRuntimeAccess(reason: string): void {
    if (this.runtime.state() === 'access_revoked'
      && this.runtime.errorCode() === reason) return;
    this.runtime.revokeAccess(reason);
  }

  runtimeRefreshAvailable(): boolean {
    return !this.runtimeEvidenceSuppressed() && this.runtime.canRefresh();
  }

  runtimeStatusLabel(): string {
    if (this.runtimeEvidenceSuppressed()) {
      return 'Der ungespeicherte Draft ist nicht an die vorhandene Runtime-Evidenz gebunden.';
    }
    switch (this.runtime.state()) {
      case 'detached':
        return 'Kein geladener Workflow ist mit einer Hub-Runtime verbunden.';
      case 'loading':
        return 'Autorisierte Runtime-Evidenz wird für den geladenen Workflow geprüft.';
      case 'no_run':
        return 'Für diesen Workflow ist noch kein autorisierter Run sichtbar.';
      case 'no_run_timeout':
        return 'Für diesen Workflow wurde innerhalb des Prüfzeitraums kein Run gefunden.';
      case 'active':
        return 'Ein aktiver, vom Hub bestätigter Run ist verbunden.';
      case 'terminal':
        return 'Der vom Hub bestätigte Run ist beendet.';
      case 'access_revoked':
        return 'Der Zugriff wurde entzogen; Runtime- und Trace-Evidenz ist gelöscht.';
      case 'error':
        return 'Runtime-Evidenz konnte nicht verifiziert werden.';
    }
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
