import {
  AfterViewInit,
  Component,
  ElementRef,
  OnDestroy,
  ViewChild,
  inject,
  signal,
} from '@angular/core';

import { FormsModule } from '@angular/forms';

import {
  BpmnImportResult,
  VisualProcessApiService,
  VpGraph,
  WorkflowStatus,
} from './visual-process-api.service';

type BpmnModeler = any;

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
  template: `
<section class="bpmn-shell">
  <div class="bpmn-toolbar">
    <div class="bpmn-title">
      <strong>BPMN Blueprint Editor</strong>
      <span>{{ statusText() }}</span>
    </div>
    <div class="bpmn-actions">
      <button type="button" (click)="loadStarter()">Neu</button>
      <button type="button" (click)="exportXml()">XML</button>
      <button type="button" (click)="importXml()">Import</button>
      <button type="button" (click)="compileGraph()">Graph</button>
      <button type="button" (click)="compileWorkflowRequest()">WorkflowRequest</button>
      <button type="button" (click)="startWorkflow()">Start</button>
    </div>
  </div>

  <div class="bpmn-layout">
    <div class="bpmn-canvas" #canvas></div>

    <aside class="bpmn-panel">
      <section>
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
              <option value="end">Ende</option>
            </select>
          </label>
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
        <textarea class="bpmn-xml" rows="8" [(ngModel)]="xmlBuffer"></textarea>
      </section>

      <section>
        <h2>Ergebnis</h2>
        @if (warnings().length) {
          <ul class="bpmn-warnings">
            @for (warning of warnings(); track warning) {
              <li>{{ warning }}</li>
            }
          </ul>
        }
        <pre>{{ resultText() }}</pre>
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
pre {
  background: var(--surface-soft);
  border-radius: var(--radius-control);
  max-height: 280px;
  overflow: auto;
  padding: 8px;
  white-space: pre-wrap;
}
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
  private modeler?: BpmnModeler;
  private selectedElement?: any;
  private elementMetadata: Record<string, ElementMetadata> = {};

  xmlBuffer = STARTER_XML;
  selectedName = '';
  selectedMetadata: ElementMetadata = this.defaultMetadata();

  readonly selectedElementId = signal('');
  readonly statusText = signal('bereit');
  readonly warnings = signal<string[]>([]);
  readonly resultText = signal('');

  async ngAfterViewInit(): Promise<void> {
    const module = await import('bpmn-js/lib/Modeler');
    this.modeler = new module.default({
      container: this.canvas.nativeElement,
    });
    this.attachSelectionListener();
    await this.loadStarter();
  }

  ngOnDestroy(): void {
    this.modeler?.destroy();
  }

  async loadStarter(): Promise<void> {
    await this.importIntoModeler(STARTER_XML);
    this.xmlBuffer = STARTER_XML;
    this.resultText.set('');
    this.warnings.set([]);
  }

  async exportXml(): Promise<void> {
    if (!this.modeler) return;
    const result = await this.modeler.saveXML({ format: true });
    this.xmlBuffer = result.xml;
    this.statusText.set('XML exportiert');
  }

  async importXml(): Promise<void> {
    await this.importIntoModeler(this.xmlBuffer);
  }

  compileGraph(): void {
    this.withCurrentXml(xml => {
      this.api.importBpmn(xml).subscribe({
        next: result => this.handleGraphResult(this.applyMetadata(result)),
        error: err => this.showError(err),
      });
    });
  }

  compileWorkflowRequest(): void {
    this.withCurrentGraph(graph => {
      this.api.compileWorkflowRequest(graph, { policy_scope: { source: 'bpmn_blueprint_editor' } }).subscribe({
        next: result => {
          this.resultText.set(JSON.stringify(result.workflow_request, null, 2));
          this.warnings.set(result.errors || []);
          this.statusText.set('WorkflowRequest kompiliert');
        },
        error: err => this.showError(err),
      });
    });
  }

  startWorkflow(): void {
    this.withCurrentGraph(graph => {
      this.api.startWorkflowFromGraph(graph, { policy_scope: { source: 'bpmn_blueprint_editor' } }).subscribe({
        next: status => this.showStatus(status),
        error: err => this.showError(err),
      });
    });
  }

  updateName(name: string): void {
    if (!this.modeler || !this.selectedElement) return;
    const modeling = this.modeler.get('modeling');
    modeling.updateProperties(this.selectedElement, { name });
  }

  persistSelectedMetadata(): void {
    const id = this.selectedElementId();
    if (!id) return;
    this.elementMetadata[id] = { ...this.selectedMetadata };
  }

  private attachSelectionListener(): void {
    const eventBus = this.modeler?.get('eventBus');
    eventBus?.on('selection.changed', (event: any) => {
      this.selectedElement = event.newSelection?.[0];
      const id = this.selectedElement?.id || '';
      this.selectedElementId.set(id);
      this.selectedName = this.selectedElement?.businessObject?.name || '';
      this.selectedMetadata = this.elementMetadata[id] || this.defaultMetadataForElement(this.selectedElement);
    });
  }

  private async importIntoModeler(xml: string): Promise<void> {
    if (!this.modeler) return;
    try {
      await this.modeler.importXML(xml);
      const canvas = this.modeler.get('canvas');
      canvas.zoom('fit-viewport');
      this.statusText.set('Diagramm geladen');
    } catch (err) {
      this.showError(err);
    }
  }

  private withCurrentXml(next: (xml: string) => void): void {
    this.exportXml().then(() => next(this.xmlBuffer)).catch(err => this.showError(err));
  }

  private withCurrentGraph(next: (graph: VpGraph) => void): void {
    this.withCurrentXml(xml => {
      this.api.importBpmn(xml).subscribe({
        next: result => next(this.applyMetadata(result).graph),
        error: err => this.showError(err),
      });
    });
  }

  private handleGraphResult(result: BpmnImportResult): void {
    this.resultText.set(JSON.stringify(result.graph, null, 2));
    this.warnings.set(result.warnings || []);
    this.statusText.set('Graph kompiliert');
  }

  private showStatus(status: WorkflowStatus): void {
    this.resultText.set(JSON.stringify(status, null, 2));
    this.statusText.set(`Workflow ${status.status}`);
  }

  private applyMetadata(result: BpmnImportResult): BpmnImportResult {
    const graph = {
      ...result.graph,
      steps: result.graph.steps.map(step => {
        const metadata = this.elementMetadata[step.id];
        if (!metadata) return step;
        return {
          ...step,
          kind: metadata.kind || step.kind,
          role: metadata.role || step.role,
          gate: metadata.gate,
          metadata: {
            ...(step.metadata || {}),
            policy_scope: this.parsePolicyScope(metadata.policyScope),
            allowed_tools: this.parseAllowedTools(metadata.allowedTools),
          },
        };
      }),
    };
    return { ...result, graph };
  }

  private parsePolicyScope(value: string): Record<string, unknown> {
    if (!value.trim()) return {};
    try {
      const parsed = JSON.parse(value);
      return typeof parsed === 'object' && parsed !== null ? parsed : {};
    } catch {
      return {};
    }
  }

  private parseAllowedTools(value: string): string[] {
    return value.split(',').map(item => item.trim()).filter(Boolean);
  }

  private defaultMetadataForElement(element: any): ElementMetadata {
    const type = String(element?.type || '');
    if (type.includes('StartEvent')) return { ...this.defaultMetadata(), kind: 'start' };
    if (type.includes('EndEvent')) return { ...this.defaultMetadata(), kind: 'end' };
    if (type.includes('UserTask')) return { ...this.defaultMetadata(), kind: 'human_task', gate: true };
    if (type.includes('BusinessRuleTask')) return { ...this.defaultMetadata(), kind: 'review' };
    if (type.includes('ScriptTask')) return { ...this.defaultMetadata(), kind: 'coding' };
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
    const detail = (err as any)?.error || (err as any)?.message || err;
    this.resultText.set(JSON.stringify(detail, null, 2));
    this.statusText.set('Fehler');
  }
}
