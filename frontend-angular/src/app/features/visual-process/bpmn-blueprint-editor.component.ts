import {
  AfterViewInit,
  Component,
  ChangeDetectorRef,
  ElementRef,
  OnDestroy,
  ViewChild,
  inject,
  computed,
  signal,
  ViewEncapsulation,
} from '@angular/core';

import { FormsModule } from '@angular/forms';
import { Subscription, take, timeout } from 'rxjs';
import type BpmnModeler from 'bpmn-js/lib/Modeler';
import type { BpmnElement } from 'bpmn-js/lib/Modeler';
import { ANANTA_MODDLE, bpmnMetadataExtension, readBpmnMetadata } from './bpmn-metadata';
import { bpmnRecord, bpmnRuntimeView, bpmnRuntimeMarker, BpmnRuntimeView } from './bpmn-runtime-view';
import { loadBpmnRuntime } from './bpmn-runtime-loader';

import {
  BpmnImportResult,
  BpmnCapabilities,
  BpmnExecutionSupport,
  BpmnWorkflowCommand,
  WorkflowPreflight,
  VisualProcessApiService,
  VpGraph,
  WorkflowStatus,
} from './visual-process-api.service';

interface EditorOperation { revision: number; id: number; }

interface ElementMetadata {
  kind: string;
  role: string;
  gate: boolean;
  policyScope: string;
  allowedTools: string;
}

const STARTER_XML = `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
                  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
                  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
                  xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
                  id="Definitions_process_designer"
                  targetNamespace="https://ananta.local/workflows">
  <bpmn:process id="vp_bpmn_blueprint" name="Neuer Workflow" isExecutable="false">
    <bpmn:startEvent id="StartEvent_1" name="Start" />
    <bpmn:serviceTask id="Task_1" name="Planen" />
    <bpmn:userTask id="Task_2" name="Freigabe" />
    <bpmn:endEvent id="EndEvent_1" name="Ende" />
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Task_1" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="Task_2" />
    <bpmn:sequenceFlow id="Flow_3" sourceRef="Task_2" targetRef="EndEvent_1" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="Diagram_1">
    <bpmndi:BPMNPlane id="Plane_1" bpmnElement="vp_bpmn_blueprint">
      <bpmndi:BPMNShape id="Shape_StartEvent_1" bpmnElement="StartEvent_1">
        <dc:Bounds x="160" y="180" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Shape_Task_1" bpmnElement="Task_1">
        <dc:Bounds x="260" y="160" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Shape_Task_2" bpmnElement="Task_2">
        <dc:Bounds x="460" y="160" width="120" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Shape_EndEvent_1" bpmnElement="EndEvent_1">
        <dc:Bounds x="660" y="180" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Edge_Flow_1" bpmnElement="Flow_1">
        <di:waypoint x="196" y="198" />
        <di:waypoint x="260" y="200" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Edge_Flow_2" bpmnElement="Flow_2">
        <di:waypoint x="380" y="200" />
        <di:waypoint x="460" y="200" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Edge_Flow_3" bpmnElement="Flow_3">
        <di:waypoint x="580" y="200" />
        <di:waypoint x="660" y="198" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>`;

@Component({
  standalone: true,
  selector: 'app-bpmn-blueprint-editor',
  imports: [FormsModule],
  encapsulation: ViewEncapsulation.None,
  template: `
<section class="bpmn-shell">
  <div class="bpmn-toolbar">
    <div class="bpmn-title">
      <strong>BPMN Blueprint Editor</strong>
      <span>{{ statusText() }}</span>
    </div>
    <div class="bpmn-actions">
      <button type="button" (click)="loadStarter()" [disabled]="importing() || starting()">Neu</button>
      <button type="button" (click)="undo()" [disabled]="!canUndo() || importing() || starting()">Rückgängig</button>
      <button type="button" (click)="redo()" [disabled]="!canRedo() || importing() || starting()">Wiederholen</button>
      <button type="button" (click)="exportXml()" [disabled]="importing()">XML</button>
      <button type="button" (click)="importXml()" [disabled]="importing() || starting()">Import</button>
      <button type="button" (click)="compileGraph()" [disabled]="importing() || starting()">Graph</button>
      <button type="button" (click)="compileWorkflowRequest()" [disabled]="importing() || starting()">WorkflowRequest</button>
      <button type="button" (click)="startWorkflow()" [disabled]="!canStart() || starting() || controlling()">Start</button>
    </div>
  </div>

  <div class="bpmn-layout">
    <div class="bpmn-canvas" #canvas [attr.inert]="importing() || starting() ? '' : null"></div>

    <aside class="bpmn-panel">
      <section [attr.inert]="importing() || starting() ? '' : null">
        <h2>Element</h2>
        @if (selectedElementId()) {
          <label>
            Name
            <input [(ngModel)]="selectedName" (ngModelChange)="updateName($event)" />
          </label>
          <label>
            Task-Art
            <select [(ngModel)]="selectedMetadata.kind" (ngModelChange)="persistSelectedMetadata()">
              <option value="start">Start</option>
              <option value="tool_task">Tool Task</option>
              <option value="coding">Coding</option>
              <option value="analysis">Analyse</option>
              <option value="review">Review</option>
              <option value="human_task">Human Task</option>
              <option value="decision">Entscheidung</option>
              <option value="parallel">Parallel</option>
              <option value="end">Ende</option>
            </select>
          </label>
          @if (metadataError()) { <p role="alert">{{ metadataError() }}</p> }
          <label>
            Rolle
            <input [(ngModel)]="selectedMetadata.role" (ngModelChange)="persistSelectedMetadata()" />
          </label>
          <label>
            Policy Scope JSON
            <textarea rows="4" [(ngModel)]="selectedMetadata.policyScope" (ngModelChange)="persistSelectedMetadata()"></textarea>
          </label>
          <label>
            Erlaubte Tools
            <input [(ngModel)]="selectedMetadata.allowedTools" (ngModelChange)="persistSelectedMetadata()" placeholder="read_file, run_tests" />
          </label>
          <label class="bpmn-check">
            <input type="checkbox" [(ngModel)]="selectedMetadata.gate" (ngModelChange)="persistSelectedMetadata()" />
            Gate
          </label>
        } @else {
          <p>Kein Element ausgewählt.</p>
        }
      </section>

      <section>
        <h2>Import / Export</h2>
        <textarea class="bpmn-xml" aria-label="BPMN XML" rows="8" [(ngModel)]="xmlBuffer"></textarea>
        <p>XML-Änderungen werden mit Import ins Diagramm übernommen.</p>
      </section>

      <section>
        <h2>Ergebnis</h2>
        <p>{{ capabilityMessage() }}</p>
        @if (capabilities(); as catalog) {
          <p>Erforderliche Hub-Freigabe: {{ catalog.required_feature_flag }}.
            Runtime-Nachweis laut Katalog: {{ catalog.runtime_verified ? 'bestätigt' : 'nicht bestätigt' }}.</p>
          <ul>
            @for (runtime of runtimeContracts(); track runtime[0]) {
              <li>{{ runtime[0] }}: {{ runtime[1] }}</li>
            }
          </ul>
        }
        <p role="status">{{ executionText() }}</p>
        @for (issue of supportIssues(); track $index) {
          <button type="button" (click)="focusIssue(issue.element_id)">
            {{ issue.element_id }}: {{ issue.reason_code }} {{ issue.detail }}
          </button>
        }
        @if (warnings().length) {
          <ul class="bpmn-warnings">
            @for (warning of warnings(); track warning) {
              <li>{{ warning }}</li>
            }
          </ul>
        }
        <pre>{{ resultText() }}</pre>
      </section>

      <section class="bpmn-runtime">
        <h2>Hub-Laufzeitstatus</h2>
        <label>Workflow-ID <input [(ngModel)]="workflowId" (ngModelChange)="clearRuntime()" [disabled]="starting()" /></label>
        <button type="button" (click)="refreshRuntime()" [disabled]="!workflowId.trim() || starting() || controlling()">Status laden</button>
        <div class="bpmn-actions">
          <button type="button" (click)="controlWorkflow('resume')" [disabled]="!canControl('resume')">Resume</button>
          <button type="button" (click)="controlWorkflow('retry')" [disabled]="!canControl('retry')">Retry</button>
          <button type="button" (click)="controlWorkflow('cancel')" [disabled]="!canControl('cancel')">Cancel</button>
        </div>
        <p>{{ runtimeMessage() }}</p>
        @if (runtimeView(); as runtime) {
          <dl>
            <dt>Workflow</dt><dd>{{ runtime.workflowId }}</dd>
            <dt>Backend</dt><dd>{{ runtime.backend || 'Nicht gemeldet' }}</dd>
            <dt>Status laut Hub</dt><dd>{{ runtime.status || 'Nicht gemeldet' }}</dd>
            <dt>Run-ID</dt><dd>{{ runtime.runId || 'Nicht gemeldet' }}</dd>
            <dt>Plan-Hash</dt><dd>{{ runtime.planHash || 'Nicht gemeldet' }}</dd>
            <dt>Definitions-Hash</dt><dd>{{ runtime.definitionHash || 'Nicht gemeldet' }}</dd>
            <dt>Statusrevision</dt><dd>{{ runtime.revision || 'Nicht gemeldet' }}</dd>
          </dl>
          <p>{{ runtimeMatchesDefinition() ? 'Hub-Definition stimmt mit dem geprüften Diagramm überein.' : 'Zuordnung zur aktuellen Editor-Definition nicht bestätigt. Keine Canvas-Markierung.' }}</p>
          <p>Kommandos betreffen ausschließlich diesen Hub-Lauf. Fehlt eine Hub-Freigabe, bleibt das Kommando gesperrt.</p>
          <table>
            <caption>Vom Hub gemeldete Schritte</caption>
            <thead><tr><th>Schritt / Element</th><th>Status laut Hub</th><th>Task / Aktivierung / Iteration</th></tr></thead>
            <tbody>@for (step of runtime.steps; track $index) {
              <tr [attr.data-element-id]="step.elementId"><td>{{ step.id }} / {{ step.elementId }}</td><td>{{ step.status }}</td>
                <td>{{ step.taskId || '–' }} / {{ step.activationId || '–' }} / {{ step.iteration || '–' }}</td></tr>
            }</tbody>
          </table>
          <table class="bpmn-runtime-edges">
            <caption>Vom Hub gemeldete Kanten</caption>
            <thead><tr><th>Kante</th><th>Status</th><th>Quelle</th></tr></thead>
            <tbody>@for (edge of runtime.edges; track edge.id) {
              <tr><td>{{ edge.id }}</td><td>{{ edge.status }}</td><td>{{ edge.source }}</td></tr>
            }</tbody>
          </table>
        }
      </section>
    </aside>
  </div>
</section>
  `,
  styles: [`
.bpmn-shell {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: calc(100vh - 96px);
}
.bpmn-toolbar {
  align-items: center;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 12px;
}
.bpmn-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.bpmn-title span {
  color: var(--muted);
  font-size: 12px;
}
.bpmn-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.bpmn-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 16px;
  min-height: 720px;
}
.bpmn-canvas {
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  min-height: 720px;
  overflow: hidden;
}
.bpmn-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.bpmn-panel section {
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  padding: 12px;
}
.bpmn-panel h2 {
  font-size: 14px;
  margin: 0 0 10px;
}
.bpmn-panel label {
  display: grid;
  gap: 4px;
  font-size: 12px;
  margin-bottom: 10px;
}
.bpmn-check {
  align-items: center;
  display: flex !important;
  gap: 8px !important;
}
.bpmn-xml {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  width: 100%;
}
.bpmn-warnings {
  color: var(--warning);
  margin: 0 0 8px;
  padding-left: 18px;
}
.bpmn-shell pre {
  background: var(--surface-soft);
  border-radius: var(--radius-control);
  max-height: 280px;
  overflow: auto;
  padding: 8px;
  white-space: pre-wrap;
}
.bpmn-runtime dd { margin-left: 0; overflow-wrap: anywhere; }
.bpmn-runtime table { width: 100%; text-align: left; overflow-wrap: anywhere; }
.bpmn-canvas .bpmn-unsupported .djs-visual > :first-child { stroke: #b45309 !important; stroke-width: 3px !important; }
.bpmn-canvas .bpmn-runtime-selected .djs-visual > :first-child { stroke: #2563eb !important; stroke-width: 4px !important; }
.bpmn-canvas .bpmn-runtime-running .djs-visual > :first-child { stroke: #2563eb !important; stroke-width: 3px !important; }
.bpmn-canvas .bpmn-runtime-waiting .djs-visual > :first-child { stroke: #a16207 !important; stroke-width: 3px !important; }
.bpmn-canvas .bpmn-runtime-completed .djs-visual > :first-child { stroke: #15803d !important; stroke-width: 3px !important; }
.bpmn-canvas .bpmn-runtime-failed .djs-visual > :first-child,
.bpmn-canvas .bpmn-runtime-blocked .djs-visual > :first-child { stroke: #b91c1c !important; stroke-width: 3px !important; }
.bpmn-canvas .bpmn-runtime-skipped .djs-visual > :first-child,
.bpmn-canvas .bpmn-runtime-cancelled .djs-visual > :first-child { stroke: #64748b !important; stroke-dasharray: 5 3 !important; }
@media (max-width: 980px) {
  .bpmn-layout {
    grid-template-columns: 1fr;
  }
  .bpmn-panel {
    order: -1;
  }
}
  `],
})
export class BpmnBlueprintEditorComponent implements AfterViewInit, OnDestroy {
  @ViewChild('canvas', { static: true }) canvas!: ElementRef<HTMLDivElement>;

  private api = inject(VisualProcessApiService);
  private changeDetector = inject(ChangeDetectorRef);
  private modeler?: BpmnModeler;
  private selectedElement?: BpmnElement;
  private revision = 0;
  private operationId = 0;
  private destroyed = false;
  private writingMetadata = false;
  private documentWarnings: string[] = [];
  private compiledGraph?: VpGraph;
  private subscriptions = new Subscription();
  private markedElements: BpmnElement[] = [];
  private runtimeRequest = 0;
  private preflight?: WorkflowPreflight;
  private runtimeMarkers: { element: BpmnElement; marker: string }[] = [];
  private readonly workflowOptions = { policy_scope: { source: 'bpmn_blueprint_editor' } };

  xmlBuffer = STARTER_XML;
  selectedName = '';
  selectedMetadata: ElementMetadata = this.defaultMetadata();
  workflowId = '';

  readonly selectedElementId = signal('');
  readonly statusText = signal('bereit');
  readonly warnings = signal<string[]>([]);
  readonly resultText = signal('');
  readonly canStart = signal(false);
  readonly canUndo = signal(false);
  readonly canRedo = signal(false);
  readonly importing = signal(false);
  readonly starting = signal(false);
  readonly controlling = signal(false);
  readonly editorDefinitionHash = signal('');
  readonly metadataError = signal('');
  readonly supportIssues = signal<BpmnExecutionSupport['issues']>([]);
  readonly capabilities = signal<BpmnCapabilities | null>(null);
  readonly runtimeContracts = computed(() => Object.entries(this.capabilities()?.runtimes || {}));
  readonly capabilityMessage = signal('Hub-Capabilities noch nicht geladen.');
  readonly executionText = signal('Nicht geprüft. WorkflowRequest kompilieren; die Hub-Policy entscheidet über den Start.');
  readonly runtimeView = signal<BpmnRuntimeView | null>(null);
  readonly runtimeMessage = signal('Noch kein Laufzeitstatus vom Hub geladen.');
  readonly runtimeMatchesDefinition = computed(() => !!this.editorDefinitionHash()
    && this.runtimeView()?.definitionHash === this.editorDefinitionHash());

  async ngAfterViewInit(): Promise<void> {
    try {
      const module = await import('bpmn-js/lib/Modeler');
      if (this.destroyed) return;
      this.modeler = new module.default({
        container: this.canvas.nativeElement,
        moddleExtensions: { ananta: ANANTA_MODDLE },
      });
      this.modeler.get('eventBus').on('selection.changed', () => this.syncSelection());
      this.modeler.get('eventBus').on('commandStack.changed', () => {
        this.invalidateCompilation();
        this.syncHistory();
        if (!this.writingMetadata) this.syncSelection();
      });
      this.subscriptions.add(this.api.getBpmnCapabilities().pipe(take(1)).subscribe({
        next: catalog => {
          this.capabilities.set(catalog);
          this.capabilityMessage.set('Hub-Supportkatalog geladen; aktuelle Runtime und Policy werden vor Start geprüft.');
        },
        error: () => this.capabilityMessage.set('Hub-Supportkatalog nicht verfügbar. Keine Runtime-Bestätigung.'),
      }));
      await this.loadStarter();
    } catch (err) {
      if (!this.destroyed) this.showError(err);
    }
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.subscriptions.unsubscribe();
    this.modeler?.destroy();
    this.modeler = undefined;
  }

  async loadStarter(): Promise<void> {
    if (await this.importIntoModeler(STARTER_XML)) this.xmlBuffer = STARTER_XML;
  }

  async exportXml(): Promise<void> {
    if (!this.modeler || this.importing()) return;
    const revision = this.revision;
    const buffer = this.xmlBuffer;
    try {
      const result = await this.modeler.saveXML({ format: true });
      if (this.destroyed || revision !== this.revision || buffer !== this.xmlBuffer) return;
      if (!result.xml) throw new Error('Kein BPMN XML exportiert');
      this.xmlBuffer = result.xml;
      this.statusText.set('XML exportiert');
      this.changeDetector.markForCheck();
    } catch (err) {
      if (!this.destroyed && revision === this.revision) this.showError(err);
    }
  }

  async importXml(): Promise<void> {
    await this.importIntoModeler(this.xmlBuffer);
  }

  compileGraph(): void {
    if (!this.canInspect()) return;
    const operation = this.beginOperation();
    this.withCurrentGraph((_graph, result) => {
      this.resultText.set(JSON.stringify(result.graph, null, 2));
      this.statusText.set('Graph kompiliert');
    }, operation);
  }

  compileWorkflowRequest(): void {
    if (!this.canInspect()) return;
    const operation = this.beginOperation();
    this.withCurrentGraph((graph, imported) => {
      if (!this.showSupport(imported)) return;
      this.subscriptions.add(this.api.compileWorkflowRequest(graph, this.workflowOptions).pipe(take(1)).subscribe({
        next: result => {
          if (!this.isCurrent(operation)) return;
          this.resultText.set(JSON.stringify(result.workflow_request, null, 2));
          this.setWarnings([...(imported.warnings || []), ...(result.errors || [])]);
          const valid = result.validation?.valid === true && Array.isArray(result.errors) && result.errors.length === 0;
          this.executionText.set(this.documentWarnings.length
            ? 'Import mit möglichem Datenverlust: Start gesperrt. Vollständiges XML erneut importieren oder ein neues Diagramm anlegen.'
            : valid
              ? 'Kompilierbar. Hub-Preflight für Runtime und Policy läuft; Start gesperrt.'
              : 'Nicht kompilierbar: Hub-Validierung fehlgeschlagen. Start gesperrt.');
          this.statusText.set(valid ? 'WorkflowRequest kompiliert' : 'Kompilierung abgelehnt');
          if (valid && !this.documentWarnings.length) this.checkReadiness(graph, operation);
        },
        error: err => { if (this.isCurrent(operation)) this.showError(err); },
      }));
    }, operation);
  }

  startWorkflow(): void {
    if (!this.canStart() || !this.compiledGraph || !this.preflight || !this.canInspect()) return;
    // Start exactly the server-compiled snapshot, never a later XML buffer.
    const graph = this.compiledGraph;
    const preflight = this.preflight;
    const operation = this.beginOperation();
    this.starting.set(true);
    this.workflowId = graph.id;
    this.clearRuntime();
    const runtimeRequest = this.runtimeRequest;
    this.subscriptions.add(this.api.startWorkflowFromGraph(graph, {
      ...this.workflowOptions, expected_plan_hash: preflight.plan_hash, expected_definition_hash: preflight.definition_hash,
    }).pipe(take(1), timeout(15000)).subscribe({
      next: status => {
        this.starting.set(false);
        if (!this.isCurrent(operation) || runtimeRequest !== this.runtimeRequest) return;
        if (status.workflow_id !== preflight.workflow_id || status['plan_hash'] !== preflight.plan_hash
          || status['definition_hash'] !== preflight.definition_hash) {
          this.runtimeMessage.set('Hub-Startantwort passt nicht zur geprüften Definition. Status erneut laden.');
          return;
        }
        this.workflowId = status.workflow_id;
        this.showStatus(status);
        this.refreshRuntime();
      },
      error: err => {
        this.starting.set(false);
        if (this.isCurrent(operation) && runtimeRequest === this.runtimeRequest) this.showError(err);
      },
      complete: () => this.starting.set(false),
    }));
  }

  undo(): void { if (!this.importing() && !this.starting()) this.modeler?.get('commandStack').undo(); }
  redo(): void { if (!this.importing() && !this.starting()) this.modeler?.get('commandStack').redo(); }

  focusIssue(id: string): void {
    const element = this.modeler?.get('elementRegistry').get(id);
    if (element) this.modeler?.get('selection').select(element);
  }

  clearRuntime(): void {
    this.runtimeRequest += 1;
    this.runtimeView.set(null);
    this.clearRuntimeMarkers();
    this.runtimeMessage.set('Noch kein Laufzeitstatus vom Hub geladen.');
  }

  refreshRuntime(): void {
    const id = this.workflowId.trim();
    if (!id || this.starting() || this.controlling() || this.destroyed) return;
    this.clearRuntime();
    const request = this.runtimeRequest;
    this.runtimeMessage.set('Status wird vom Hub geladen.');
    const current = () => !this.destroyed && request === this.runtimeRequest && id === this.workflowId.trim();
    this.subscriptions.add(loadBpmnRuntime(this.api, id, current).subscribe({
      next: ({ identityMatches, consistent, view, historyAvailable }) => {
        if (!current()) return;
        if (!identityMatches) {
          this.runtimeMessage.set('Hub-Antwort gehört zu einem anderen Workflow.');
          return;
        }
        if (!consistent) {
          this.runtimeMessage.set('Hub-Revision während des Ladens geändert. Status erneut laden.');
          return;
        }
        this.runtimeView.set(view);
        this.runtimeMessage.set('Statusmeldung des Hubs. Sie allein ist kein Ausführungs- oder Release-Nachweis.');
        this.paintRuntime();
        if (!historyAvailable) this.runtimeMessage.update(message => message + ' Ereignisse nicht verfügbar; keine Pfadrekonstruktion.');
      },
      error: () => { if (current()) this.runtimeMessage.set('Laufzeitstatus konnte nicht geladen werden.'); },
    }));
  }

  canControl(command: BpmnWorkflowCommand): boolean {
    const runtime = this.runtimeView();
    return !this.destroyed && !this.starting() && !this.controlling()
      && runtime?.workflowId === this.workflowId.trim() && !!runtime?.commands.includes(command);
  }

  controlWorkflow(command: BpmnWorkflowCommand): void {
    const runtime = this.runtimeView();
    if (!this.canControl(command) || !runtime?.commandBinding) return;
    const id = runtime.workflowId;
    const request = ++this.runtimeRequest;
    const current = () => !this.destroyed && request === this.runtimeRequest && id === this.workflowId.trim();
    this.controlling.set(true);
    // This is a command deduplication key, never a source/run evidence identity.
    const commandId = 'bpmn-command-' + globalThis.crypto.getRandomValues(new Uint32Array(4)).join('-');
    this.subscriptions.add(this.api.controlBpmnWorkflow(id, command, runtime.commandBinding, commandId).pipe(take(1), timeout(15000)).subscribe({
      next: status => {
        this.controlling.set(false);
        if (!current()) return;
        if (status.workflow_id !== id || status['run_id'] !== runtime.runId || status['plan_hash'] !== runtime.planHash
          || status['definition_hash'] !== runtime.definitionHash || typeof status['revision'] !== 'number'
          || status['revision'] < runtime.commandBinding!.expected_revision) {
          this.clearRuntime();
          this.runtimeMessage.set('Kommandoantwort hat eine abweichende Laufbindung. Status erneut laden.');
          return;
        }
        this.refreshRuntime();
      },
      error: () => {
        this.controlling.set(false);
        if (!current()) return;
        this.clearRuntime();
        this.runtimeMessage.set('Hub-Kommando abgelehnt oder Antwort nicht bestätigt. Status erneut laden; nicht automatisch wiederholen.');
      },
      complete: () => this.controlling.set(false),
    }));
  }

  updateName(name: string): void {
    if (!this.modeler || !this.selectedElement || this.importing() || this.starting()) return;
    this.modeler.get('modeling').updateProperties(this.selectedElement, { name });
  }

  persistSelectedMetadata(): void {
    if (!this.modeler || !this.selectedElement || this.importing() || this.starting()) return;
    try {
      const extensionElements = bpmnMetadataExtension(this.modeler.get('moddle'), this.selectedElement.businessObject, {
        kind: this.selectedMetadata.kind,
        role: this.selectedMetadata.role,
        gate: this.selectedMetadata.gate,
        policy_scope: this.parsePolicyScope(this.selectedMetadata.policyScope),
        allowed_tools: this.parseAllowedTools(this.selectedMetadata.allowedTools),
      });
      this.metadataError.set('');
      this.writingMetadata = true;
      this.modeler.get('modeling').updateProperties(this.selectedElement, { extensionElements });
    } catch (err) {
      this.invalidateCompilation();
      this.metadataError.set(err instanceof Error ? err.message : 'Ungültige Metadaten');
    } finally {
      this.writingMetadata = false;
    }
  }

  private syncSelection(): void {
    this.changeDetector.markForCheck();
    const selection = this.modeler?.get('selection').get() || [];
    const selected = selection.length === 1 ? selection[0] : undefined;
    this.selectedElement = selected?.labelTarget || selected;
    this.selectedElementId.set(this.selectedElement?.id || '');
    this.selectedName = this.selectedElement?.businessObject.name || '';
    this.selectedMetadata = this.defaultMetadataForElement(this.selectedElement);
    this.metadataError.set('');
    if (!this.selectedElement) return;
    try {
      const metadata = readBpmnMetadata(this.selectedElement.businessObject);
      this.selectedMetadata = {
        kind: String(metadata['kind'] ?? this.selectedMetadata.kind),
        role: String(metadata['role'] ?? this.selectedMetadata.role),
        gate: typeof metadata['gate'] === 'boolean' ? metadata['gate'] : this.selectedMetadata.gate,
        policyScope: JSON.stringify(metadata['policy_scope'] ?? { source: 'bpmn_blueprint_editor' }),
        allowedTools: Array.isArray(metadata['allowed_tools']) ? metadata['allowed_tools'].join(', ') : '',
      };
    } catch (err) {
      this.metadataError.set(err instanceof Error ? err.message : 'Ungültige Metadaten');
      this.canStart.set(false);
    }
  }

  private syncHistory(): void {
    const stack = this.modeler?.get('commandStack');
    this.canUndo.set(stack?.canUndo() ?? false);
    this.canRedo.set(stack?.canRedo() ?? false);
  }

  private setWarnings(warnings: string[]): void {
    this.warnings.set([...new Set([...this.documentWarnings, ...warnings])]);
  }

  private setSupportIssues(issues: BpmnExecutionSupport['issues']): void {
    const canvas = this.modeler?.get('canvas');
    for (const element of this.markedElements) canvas?.removeMarker(element, 'bpmn-unsupported');
    this.markedElements = [];
    this.supportIssues.set(issues);
    for (const issue of issues) {
      const element = this.modeler?.get('elementRegistry').get(issue.element_id);
      if (element) {
        canvas?.addMarker(element, 'bpmn-unsupported');
        this.markedElements.push(element);
      }
    }
  }

  private canInspect(): boolean {
    return !this.destroyed && !this.importing() && !this.starting() && !this.controlling() && !this.metadataError();
  }

  private beginOperation(): EditorOperation {
    this.canStart.set(false);
    this.compiledGraph = undefined;
    this.preflight = undefined;
    return { revision: this.revision, id: ++this.operationId };
  }

  private isCurrent(operation: EditorOperation): boolean {
    return !this.destroyed && operation.revision === this.revision && operation.id === this.operationId;
  }

  private async importIntoModeler(xml: string): Promise<boolean> {
    // bpmn-js import mutates its canvas asynchronously, so imports must not overlap.
    if (!this.modeler || this.destroyed || this.importing() || this.starting()) return false;
    this.importing.set(true);
    this.invalidateCompilation();
    try {
      const result = await this.modeler.importXML(xml);
      if (this.destroyed) return false;
      this.documentWarnings = result.warnings.map(warning => 'BPMN-Import (möglicher Datenverlust): ' + warning.message);
      this.setWarnings([]);
      this.syncSelection();
      this.syncHistory();
      this.modeler.get('canvas').zoom('fit-viewport');
      this.statusText.set('Diagramm geladen');
      if (this.documentWarnings.length) this.executionText.set('Import mit möglichem Datenverlust: Start gesperrt.');
      return true;
    } catch (err) {
      if (!this.destroyed) {
        this.documentWarnings = ['BPMN-Import fehlgeschlagen. Vor dem Start ein gültiges Diagramm importieren.'];
        this.setWarnings([]);
        this.syncSelection();
        this.syncHistory();
        this.showError(err);
      }
      return false;
    } finally {
      if (!this.destroyed) this.importing.set(false);
    }
  }

  private async withCurrentXml(next: (xml: string) => void, operation: EditorOperation): Promise<void> {
    if (!this.modeler) return;
    try {
      const result = await this.modeler.saveXML({ format: true });
      if (!this.isCurrent(operation)) return;
      if (!result.xml) throw new Error('Kein BPMN XML exportiert');
      next(result.xml);
    } catch (err) {
      if (this.isCurrent(operation)) this.showError(err);
    }
  }

  private withCurrentGraph(next: (graph: VpGraph, result: BpmnImportResult) => void, operation: EditorOperation): void {
    void this.withCurrentXml(xml => {
      this.subscriptions.add(this.api.importBpmn(xml).pipe(take(1)).subscribe({
        next: result => {
          if (!this.isCurrent(operation)) return;
          this.setWarnings(result.warnings || []);
          this.showSupport(result);
          next(result.graph, result);
        },
        error: err => { if (this.isCurrent(operation)) this.showError(err); },
      }));
    }, operation);
  }

  private showStatus(status: WorkflowStatus): void {
    this.runtimeView.set(bpmnRuntimeView(status));
    this.paintRuntime();
    this.runtimeMessage.set('Statusmeldung des Hubs. Sie allein ist kein Ausführungs- oder Release-Nachweis.');
  }

  private invalidateCompilation(): void {
    this.revision += 1;
    this.operationId += 1;
    this.compiledGraph = undefined;
    this.preflight = undefined;
    this.editorDefinitionHash.set('');
    this.clearRuntimeMarkers();
    this.canStart.set(false);
    this.resultText.set('');
    this.setSupportIssues([]);
    this.setWarnings([]);
    this.executionText.set('Definition geändert: vor dem Start erneut WorkflowRequest kompilieren.');
  }

  private checkReadiness(graph: VpGraph, operation: EditorOperation): void {
    this.subscriptions.add(this.api.preflightWorkflowFromGraph(graph, this.workflowOptions).pipe(take(1), timeout(15000)).subscribe({
      next: result => {
        if (!this.isCurrent(operation)) return;
        const bound = result?.workflow_id === graph.id && typeof result.plan_hash === 'string'
          && /^[0-9a-f]{64}$/.test(result.plan_hash) && typeof result.definition_hash === 'string'
          && /^[0-9a-f]{64}$/.test(result.definition_hash);
        const reasonsValid = Array.isArray(result?.reason_codes) && result.reason_codes.every(reason =>
          typeof reason === 'string' && !!reason);
        const ready = bound && reasonsValid && result.ready === true && result.reason_codes.length === 0
          && typeof result.runtime_id === 'string' && !!result.runtime_id;
        this.preflight = ready ? result : undefined;
        this.compiledGraph = ready ? graph : undefined;
        this.canStart.set(ready);
        this.editorDefinitionHash.set(bound ? result.definition_hash : '');
        this.paintRuntime();
        this.setSupportIssues(reasonsValid ? result.reason_codes.map(reason => ({
          element_id: '', reason_code: reason, detail: '',
        })) : []);
        this.executionText.set(ready
          ? 'Hub-Preflight: Start für diese Definition bereit. Kein Ausführungsnachweis; der Hub prüft beim Start erneut.'
          : 'Hub-Preflight: Start gesperrt. ' + (reasonsValid && result.reason_codes.length
            ? result.reason_codes.join('; ') : 'Vollständige Bereitschaftsbestätigung fehlt.'));
      },
      error: () => {
        if (!this.isCurrent(operation)) return;
        this.canStart.set(false);
        this.executionText.set('Hub-Preflight nicht verfügbar. Start gesperrt.');
      },
    }));
  }

  private clearRuntimeMarkers(): void {
    const canvas = this.modeler?.get('canvas');
    for (const { element, marker } of this.runtimeMarkers) canvas?.removeMarker(element, marker);
    this.runtimeMarkers = [];
  }

  private paintRuntime(): void {
    this.clearRuntimeMarkers();
    const runtime = this.runtimeView();
    if (!runtime || !this.runtimeMatchesDefinition()) return;
    const states = [...runtime.steps.map(step => ({ id: step.elementId, status: step.status })), ...runtime.edges];
    for (const state of states) {
      const marker = bpmnRuntimeMarker(state.status);
      const element = this.modeler?.get('elementRegistry').get(state.id);
      if (element && marker) {
        this.modeler?.get('canvas').addMarker(element, marker);
        this.runtimeMarkers.push({ element, marker });
      }
    }
  }

  private showSupport(result: BpmnImportResult): boolean {
    this.setSupportIssues(result.execution_support?.issues || []);
    if (result.execution_support?.supported === false) {
      this.canStart.set(false);
      this.executionText.set('Bearbeitbar, aber nicht ausführbar: ' + result.execution_support.issues.map(issue => issue.element_id + ': ' + issue.reason_code).join('; '));
      return false;
    }
    if (result.execution_support?.supported !== true || result.validation?.valid !== true) {
      this.canStart.set(false);
      this.executionText.set('Bearbeitbar; gültige Hub-Bestätigung zur Ausführbarkeit fehlt. Start gesperrt.');
      return false;
    }
    if (typeof result.execution_support.definition_hash === 'string' && !this.documentWarnings.length) {
      this.editorDefinitionHash.set(result.execution_support.definition_hash);
      this.paintRuntime();
    }
    this.executionText.set('BPMN-Semantik vom Hub unterstützt. WorkflowRequest noch nicht kompiliert; Runtime und Policy noch nicht geprüft.');
    return true;
  }

  private parsePolicyScope(value: string): Record<string, unknown> {
    if (!value.trim()) return {};
    const parsed = JSON.parse(value);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('Policy Scope muss ein JSON-Objekt sein');
    return parsed;
  }

  private parseAllowedTools(value: string): string[] {
    return value.split(',').map(item => item.trim()).filter(Boolean);
  }

  private defaultMetadataForElement(element?: BpmnElement): ElementMetadata {
    const type = String(element?.type || '');
    if (type.includes('StartEvent')) return { ...this.defaultMetadata(), kind: 'start' };
    if (type.includes('EndEvent')) return { ...this.defaultMetadata(), kind: 'end' };
    if (type.includes('UserTask')) return { ...this.defaultMetadata(), kind: 'human_task', gate: true };
    if (type.includes('BusinessRuleTask')) return { ...this.defaultMetadata(), kind: 'review' };
    if (type.includes('ScriptTask')) return { ...this.defaultMetadata(), kind: 'coding' };
    if (type.includes('ParallelGateway')) return { ...this.defaultMetadata(), kind: 'parallel' };
    if (type.includes('Gateway')) return { ...this.defaultMetadata(), kind: 'decision' };
    return this.defaultMetadata();
  }

  private defaultMetadata(): ElementMetadata {
    return {
      kind: 'tool_task',
      role: 'default',
      gate: false,
      policyScope: '{"source":"bpmn_blueprint_editor"}',
      allowedTools: '',
    };
  }

  private showError(err: unknown): void {
    this.canStart.set(false);
    this.compiledGraph = undefined;
    const error = bpmnRecord(err);
    const detail = error['error'] || error['message'] || err;
    this.resultText.set(JSON.stringify(detail, null, 2));
    const payload = bpmnRecord(detail);
    const issues = Array.isArray(payload['issues']) ? payload['issues'].map(bpmnRecord).flatMap(issue =>
      typeof issue['element_id'] === 'string' && typeof issue['reason_code'] === 'string'
        ? [{ element_id: issue['element_id'], reason_code: issue['reason_code'], detail: String(issue['detail'] || '') }] : []) : [];
    this.setSupportIssues(issues);
    this.executionText.set('Prüfung fehlgeschlagen. Start gesperrt; Details siehe Ergebnis.');
    this.statusText.set('Fehler');
  }
}
