import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  EventEmitter,
  HostListener,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  ViewChild,
} from '@angular/core';

import type {
  StepPosition,
  VpGraph,
  VpRuntimeOverlay,
} from '../../visual-process/visual-process-api.service';
import {
  CaseFlowAgentCanvasEdgeProjection,
  CaseFlowAgentCanvasNodeProjection,
  CaseFlowAgentCanvasProjection,
} from './caseflow-agent-canvas.models';
import type {
  CaseFlowEdgeIdentity,
  CaseFlowEdgeTraceReadModel,
} from './caseflow-edge-trace.models';
import { projectAgentCanvas } from './caseflow-agent-canvas.mapper';
import {
  CaseFlowAgentEdgeActivityProjection,
  projectCaseFlowAgentEdgeActivity,
} from './caseflow-agent-edge-activity.mapper';
import {
  CaseFlowAgentRuntimeNodeProjection,
  CaseFlowAgentRuntimeProjection,
  projectCaseFlowAgentRuntime,
} from './caseflow-agent-runtime.mapper';

export const CASEFLOW_AGENT_NODE_WIDTH = 168;
export const CASEFLOW_AGENT_NODE_HEIGHT = 104;

const DEFAULT_VIEWPORT: Readonly<CaseFlowAgentCanvasViewport> = {
  x: 0,
  y: 0,
  scale: 1,
};
const MIN_SCALE = 0.25;
const MAX_SCALE = 2.5;
const FIT_PADDING = 48;
const FALLBACK_SURFACE_WIDTH = 800;
const FALLBACK_SURFACE_HEIGHT = 500;
const BIDIRECTIONAL_EDGE_BEND = 28;
const FEEDBACK_EDGE_BEND = 52;
const KEYBOARD_NUDGE = 10;
const KEYBOARD_NUDGE_LARGE = 50;

let componentSequence = 0;

export interface CaseFlowAgentCanvasViewport {
  readonly x: number;
  readonly y: number;
  readonly scale: number;
}

interface PointerInteraction {
  readonly kind: 'drag-node' | 'pan';
  readonly pointerId: number;
  readonly startClientX: number;
  readonly startClientY: number;
  readonly startViewport: CaseFlowAgentCanvasViewport;
  readonly stepId?: string;
  readonly startPosition?: StepPosition;
}

interface Point {
  readonly x: number;
  readonly y: number;
}

/**
 * Focused immutable graph command used by drag interactions. Only the selected
 * step and its position object are replaced; every unrelated graph value keeps
 * its identity.
 */
export function moveCaseFlowAgentNode(
  graph: VpGraph,
  stepId: string,
  position: Readonly<StepPosition>,
): VpGraph {
  const stepIndex = graph.steps.findIndex(step => step.id === stepId);
  if (stepIndex < 0) return graph;
  const current = graph.steps[stepIndex].position;
  if (current.x === position.x && current.y === position.y) return graph;

  const steps = [...graph.steps];
  steps[stepIndex] = {
    ...steps[stepIndex],
    position: { x: position.x, y: position.y },
  };
  return { ...graph, steps };
}

@Component({
  selector: 'app-caseflow-agent-canvas',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="agent-canvas" aria-label="CaseFlow Agent Canvas">
      <nav class="canvas-toolbar" aria-label="Canvas-Ansicht">
        <button type="button" (click)="zoomIn()" aria-label="Vergrößern">+</button>
        <button type="button" (click)="zoomOut()" aria-label="Verkleinern">−</button>
        <button type="button" (click)="fitToView()">Einpassen</button>
        <button type="button" (click)="resetViewport()">Ansicht zurücksetzen</button>
        <button type="button" (click)="resetLayout()">Layout zurücksetzen</button>
      </nav>

      @if (projectionError) {
        <p class="canvas-error" role="alert">{{ projectionError }}</p>
      }

      <svg
        #surface
        class="canvas-surface"
        role="application"
        tabindex="0"
        [attr.aria-label]="canvasAriaLabel"
        (pointerdown)="startCanvasPan($event)"
        (wheel)="onWheel($event)"
      >
        <defs>
          <marker
            [id]="arrowMarkerId"
            markerWidth="10"
            markerHeight="8"
            refX="9"
            refY="4"
            orient="auto"
            markerUnits="strokeWidth"
          >
            <path d="M 0 0 L 10 4 L 0 8 z" />
          </marker>
        </defs>

        @if (projection; as canvas) {
          <g class="canvas-viewport" [attr.transform]="viewportTransform">
            <g class="agent-edges" aria-label="Gerichtete Agentenbeziehungen">
              @for (edge of canvas.edges; track edge.edge_id) {
                <g
                  class="agent-edge"
                  [class.bidirectional-edge]="edge.reverse_edge_ids.length > 0"
                  [class.loop-edge]="edge.loop"
                  [class.feedback-edge]="edge.feedback"
                  [class.active-edge]="isEdgeActive(edge)"
                  [class.selected]="isEdgeSelected(edge)"
                  tabindex="0"
                  role="button"
                  [attr.aria-label]="edgeAriaLabel(edge)"
                  [attr.aria-pressed]="isEdgeSelected(edge)"
                  [attr.data-edge-id]="edge.edge_id"
                  [attr.data-direction]="edge.source_step_id + '->' + edge.target_step_id"
                  [attr.data-reverse-edge-ids]="edge.reverse_edge_ids.join(',')"
                  [attr.data-loop]="edge.loop"
                  [attr.data-feedback]="edge.feedback"
                  (pointerdown)="$event.stopPropagation()"
                  (click)="selectEdge(edge, $event)"
                  (keydown)="edgeKeydown($event, edge)"
                >
                  <path
                    class="edge-hit-target"
                    [attr.d]="edgePath(edge)"
                    aria-hidden="true"
                  />
                  <path
                    class="edge-line"
                    [attr.d]="edgePath(edge)"
                    [attr.marker-end]="arrowMarkerUrl"
                    aria-hidden="true"
                  />
                  @if (edge.label) {
                    <text
                      class="edge-label"
                      [attr.x]="edgeLabelPoint(edge).x"
                      [attr.y]="edgeLabelPoint(edge).y"
                      aria-hidden="true"
                    >{{ edge.label }}</text>
                  }
                  @if (isEdgeActive(edge)) {
                    <text
                      class="edge-activity-indicator"
                      [attr.x]="edgeLabelPoint(edge).x"
                      [attr.y]="edgeLabelPoint(edge).y + (edge.label ? 16 : 0)"
                      aria-hidden="true"
                    >↗ Aktiv</text>
                  }
                </g>
              }
            </g>

            <g class="agent-nodes" aria-label="Agenten">
              @for (node of canvas.nodes; track node.step_id) {
                <g
                  class="agent-node"
                  [class.selected]="isNodeSelected(node)"
                  [class.runtime-running]="runtimeProjection.available && runtimeFor(node).status === 'running'"
                  [class.runtime-awaiting]="runtimeProjection.available && runtimeFor(node).status === 'awaiting_approval'"
                  [class.runtime-success]="runtimeProjection.available && runtimeFor(node).status === 'success'"
                  [class.runtime-error]="runtimeProjection.available && runtimeFor(node).status === 'error'"
                  [class.runtime-unknown]="runtimeProjection.available && runtimeFor(node).status === 'unknown'"
                  [attr.transform]="nodeTransform(node)"
                  tabindex="0"
                  role="button"
                  [attr.aria-label]="nodeAriaLabel(node)"
                  [attr.aria-pressed]="isNodeSelected(node)"
                  [attr.data-step-id]="node.step_id"
                  (pointerdown)="startNodeDrag($event, node)"
                  (click)="selectNode(node, $event)"
                  (keydown)="nodeKeydown($event, node)"
                >
                  <rect class="node-card" [attr.width]="nodeWidth" [attr.height]="nodeHeight" rx="12" />
                  <text class="node-icon" x="16" y="27" aria-hidden="true">{{ node.icon }}</text>
                  <text class="node-label" x="16" y="50">{{ node.label }}</text>
                  <text class="node-role" x="16" y="70">{{ node.role }}</text>
                  @if (runtimeProjection.available) {
                    <text class="node-runtime-icon" x="16" y="91" aria-hidden="true">
                      {{ runtimeFor(node).icon }}
                    </text>
                    <text class="node-runtime-label" x="39" y="91">
                      {{ runtimeFor(node).label }}
                    </text>
                  }
                </g>
              }
            </g>
          </g>
        }
      </svg>

      @if (projection && projection.nodes.length === 0) {
        <p class="canvas-empty" role="status">Keine Agenten in diesem Prozess.</p>
      }
    </section>
  `,
  styleUrl: './caseflow-agent-canvas.component.scss',
})
export class CaseFlowAgentCanvasComponent implements OnChanges {
  @ViewChild('surface', { static: true }) surfaceRef!: ElementRef<SVGSVGElement>;

  @Input({ required: true }) graph!: VpGraph;
  @Input() runtimeOverlay: VpRuntimeOverlay | null = null;
  @Input() edgeTraceReadModel: CaseFlowEdgeTraceReadModel | null = null;
  @Input() selectedId: string | null = null;
  /** When either typed input is bound, both replace the ambiguous legacy highlight. */
  @Input() selectedNodeId: string | null | undefined = undefined;
  @Input() selectedEdgeIdentity: CaseFlowEdgeIdentity | null | undefined = undefined;
  @Output() readonly graphChange = new EventEmitter<VpGraph>();
  @Output() readonly selectedIdChange = new EventEmitter<string | null>();
  @Output() readonly nodeSelected = new EventEmitter<string>();
  @Output() readonly edgeSelected = new EventEmitter<string>();
  @Output() readonly edgeIdentitySelected = new EventEmitter<CaseFlowEdgeIdentity>();

  readonly nodeWidth = CASEFLOW_AGENT_NODE_WIDTH;
  readonly nodeHeight = CASEFLOW_AGENT_NODE_HEIGHT;
  readonly arrowMarkerId = `caseflow-agent-arrow-${++componentSequence}`;
  readonly arrowMarkerUrl = `url(#${this.arrowMarkerId})`;

  projection: CaseFlowAgentCanvasProjection | null = null;
  runtimeProjection: CaseFlowAgentRuntimeProjection = projectCaseFlowAgentRuntime('', [], null);
  edgeActivityProjection: CaseFlowAgentEdgeActivityProjection =
    projectCaseFlowAgentEdgeActivity('', [], null, null);
  projectionError = '';
  viewport: CaseFlowAgentCanvasViewport = { ...DEFAULT_VIEWPORT };

  private graphIdentity: string | null = null;
  private pointerInteraction: PointerInteraction | null = null;
  private readonly baselinePositions = new Map<string, StepPosition>();
  private readonly previewPositions = new Map<string, StepPosition>();

  get canvasAriaLabel(): string {
    const graphName = this.graph?.name || 'Unbenannter Prozess';
    return `${graphName}: Agent mit Tab fokussieren, mit Eingabe oder Leertaste auswählen, Canvas ziehen zum Verschieben`;
  }

  get viewportTransform(): string {
    return `translate(${this.viewport.x} ${this.viewport.y}) scale(${this.viewport.scale})`;
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (!changes['graph'] || !this.graph) {
      if (changes['runtimeOverlay']) {
        this.refreshRuntimeProjection();
        this.refreshEdgeActivityProjection();
      }
      if (changes['edgeTraceReadModel']) this.refreshEdgeActivityProjection();
      return;
    }
    const identityChanged = this.graphIdentity !== this.graph.id;
    this.graphIdentity = this.graph.id;
    this.previewPositions.clear();
    this.refreshProjection();
    this.refreshRuntimeProjection();
    this.refreshEdgeActivityProjection();

    if (identityChanged) {
      this.viewport = { ...DEFAULT_VIEWPORT };
      this.captureBaselinePositions();
    } else {
      this.reconcileBaselinePositions();
    }

    if (this.selectedId && !this.hasSelectableId(this.selectedId)) {
      this.selectedIdChange.emit(null);
    }
  }

  runtimeFor(node: CaseFlowAgentCanvasNodeProjection): CaseFlowAgentRuntimeNodeProjection {
    return this.runtimeProjection.nodes[node.step_id] ?? {
      step_id: node.step_id,
      status: 'unknown',
      label: 'Unbekannt',
      icon: 'help_outline',
      current: false,
      active: false,
    };
  }

  selectNode(node: CaseFlowAgentCanvasNodeProjection, event?: Event): void {
    event?.stopPropagation();
    if (this.isNodeSelected(node)) return;
    this.selectedId = node.step_id;
    this.selectedIdChange.emit(node.step_id);
    this.nodeSelected.emit(node.step_id);
  }

  selectEdge(edge: CaseFlowAgentCanvasEdgeProjection, event?: Event): void {
    event?.stopPropagation();
    if (this.isEdgeSelected(edge)) return;
    this.selectedId = edge.edge_id;
    this.selectedIdChange.emit(edge.edge_id);
    this.edgeSelected.emit(edge.edge_id);
    this.edgeIdentitySelected.emit(Object.freeze({
      edge_id: edge.edge_id,
      source_step_id: edge.source_step_id,
      target_step_id: edge.target_step_id,
    }));
  }

  isNodeSelected(node: Readonly<CaseFlowAgentCanvasNodeProjection>): boolean {
    return this.hasTypedSelection
      ? this.selectedNodeId === node.step_id
      : this.selectedId === node.step_id;
  }

  isEdgeSelected(edge: Readonly<CaseFlowAgentCanvasEdgeProjection>): boolean {
    if (!this.hasTypedSelection) return this.selectedId === edge.edge_id;
    return this.selectedEdgeIdentity !== null
      && this.selectedEdgeIdentity !== undefined
      && this.selectedEdgeIdentity.edge_id === edge.edge_id
      && this.selectedEdgeIdentity.source_step_id === edge.source_step_id
      && this.selectedEdgeIdentity.target_step_id === edge.target_step_id;
  }

  nodeKeydown(event: KeyboardEvent, node: CaseFlowAgentCanvasNodeProjection): void {
    if (isActivationKey(event)) {
      event.preventDefault();
      this.selectNode(node, event);
      return;
    }
    const direction = keyboardNudgeDirection(event.key);
    if (!direction) return;
    event.preventDefault();
    event.stopPropagation();
    this.selectNode(node);
    const distance = event.shiftKey ? KEYBOARD_NUDGE_LARGE : KEYBOARD_NUDGE;
    const current = this.positionFor(node);
    const updatedGraph = moveCaseFlowAgentNode(this.graph, node.step_id, {
      x: current.x + direction.x * distance,
      y: current.y + direction.y * distance,
    });
    if (updatedGraph !== this.graph) this.graphChange.emit(updatedGraph);
  }

  edgeKeydown(event: KeyboardEvent, edge: CaseFlowAgentCanvasEdgeProjection): void {
    if (!isActivationKey(event)) return;
    event.preventDefault();
    this.selectEdge(edge, event);
  }

  startNodeDrag(event: PointerEvent, node: CaseFlowAgentCanvasNodeProjection): void {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    this.selectNode(node);
    this.pointerInteraction = {
      kind: 'drag-node',
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startViewport: this.viewport,
      stepId: node.step_id,
      startPosition: { ...this.positionFor(node) },
    };
    capturePointer(event);
  }

  startCanvasPan(event: PointerEvent): void {
    if (event.button !== 0) return;
    event.preventDefault();
    this.pointerInteraction = {
      kind: 'pan',
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startViewport: this.viewport,
    };
    capturePointer(event);
  }

  @HostListener('document:pointermove', ['$event'])
  onPointerMove(event: PointerEvent): void {
    const interaction = this.pointerInteraction;
    if (!interaction || interaction.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - interaction.startClientX;
    const deltaY = event.clientY - interaction.startClientY;

    if (interaction.kind === 'pan') {
      this.viewport = {
        ...interaction.startViewport,
        x: interaction.startViewport.x + deltaX,
        y: interaction.startViewport.y + deltaY,
      };
      return;
    }

    if (!interaction.stepId || !interaction.startPosition) return;
    this.previewPositions.set(interaction.stepId, {
      x: interaction.startPosition.x + deltaX / interaction.startViewport.scale,
      y: interaction.startPosition.y + deltaY / interaction.startViewport.scale,
    });
  }

  @HostListener('document:pointerup', ['$event'])
  @HostListener('document:pointercancel', ['$event'])
  finishPointerInteraction(event: PointerEvent): void {
    const interaction = this.pointerInteraction;
    if (!interaction || interaction.pointerId !== event.pointerId) return;
    this.pointerInteraction = null;
    if (interaction.kind !== 'drag-node' || !interaction.stepId) return;

    if (event.type === 'pointercancel') {
      this.previewPositions.delete(interaction.stepId);
      return;
    }

    const position = this.previewPositions.get(interaction.stepId);
    if (!position) return;
    const updatedGraph = moveCaseFlowAgentNode(this.graph, interaction.stepId, position);
    if (updatedGraph !== this.graph) this.graphChange.emit(updatedGraph);
  }

  onWheel(event: WheelEvent): void {
    event.preventDefault();
    const bounds = this.surfaceBounds();
    this.zoomBy(event.deltaY < 0 ? 1.12 : 1 / 1.12, {
      x: event.clientX - bounds.left,
      y: event.clientY - bounds.top,
    });
  }

  zoomIn(): void {
    this.zoomBy(1.2);
  }

  zoomOut(): void {
    this.zoomBy(1 / 1.2);
  }

  fitToView(): void {
    const nodes = this.projection?.nodes ?? [];
    if (!nodes.length) {
      this.resetViewport();
      return;
    }
    const positions = nodes.map(node => this.positionFor(node));
    const minX = Math.min(...positions.map(position => position.x));
    const minY = Math.min(...positions.map(position => position.y));
    const maxX = Math.max(...positions.map(position => position.x + this.nodeWidth));
    const maxY = Math.max(...positions.map(position => position.y + this.nodeHeight));
    const bounds = this.surfaceBounds();
    const width = bounds.width || FALLBACK_SURFACE_WIDTH;
    const height = bounds.height || FALLBACK_SURFACE_HEIGHT;
    const contentWidth = Math.max(1, maxX - minX);
    const contentHeight = Math.max(1, maxY - minY);
    const scale = clamp(
      Math.min(
        (width - FIT_PADDING * 2) / contentWidth,
        (height - FIT_PADDING * 2) / contentHeight,
      ),
      MIN_SCALE,
      MAX_SCALE,
    );
    this.viewport = {
      scale,
      x: (width - contentWidth * scale) / 2 - minX * scale,
      y: (height - contentHeight * scale) / 2 - minY * scale,
    };
  }

  resetViewport(): void {
    this.viewport = { ...DEFAULT_VIEWPORT };
  }

  resetLayout(): void {
    this.previewPositions.clear();
    let updatedGraph = this.graph;
    for (const [stepId, position] of this.baselinePositions) {
      updatedGraph = moveCaseFlowAgentNode(updatedGraph, stepId, position);
    }
    if (updatedGraph !== this.graph) this.graphChange.emit(updatedGraph);
  }

  nodeTransform(node: CaseFlowAgentCanvasNodeProjection): string {
    const position = this.positionFor(node);
    return `translate(${position.x} ${position.y})`;
  }

  nodeAriaLabel(node: CaseFlowAgentCanvasNodeProjection): string {
    const runtime = this.runtimeProjection.available
      ? `, Runtime ${this.runtimeFor(node).label}`
      : '';
    return `Agent ${node.label}, Rolle ${node.role}${runtime}`;
  }

  edgeAriaLabel(edge: CaseFlowAgentCanvasEdgeProjection): string {
    const reverse = edge.reverse_edge_ids.length
      ? `, Gegenrichtung separat als ${edge.reverse_edge_ids.join(', ')}`
      : '';
    const activity = this.isEdgeActive(edge) ? ', Aktivität verifiziert aktiv' : '';
    return `Beziehung ${edge.edge_id}, Richtung ${edge.source_step_id} nach ${edge.target_step_id}${reverse}${activity}`;
  }

  isEdgeActive(edge: CaseFlowAgentCanvasEdgeProjection): boolean {
    return this.edgeActivityProjection.active_edge_ids.includes(edge.edge_id);
  }

  edgePath(edge: CaseFlowAgentCanvasEdgeProjection): string {
    const source = this.nodeCenter(edge.source_step_id);
    const target = this.nodeCenter(edge.target_step_id);
    if (!source || !target) return '';
    if (edge.loop) return loopPath(source);

    const endpoints = rectangleEdgeEndpoints(source, target);
    if (!edge.reverse_edge_ids.length && !edge.feedback) {
      return `M ${endpoints.source.x} ${endpoints.source.y} L ${endpoints.target.x} ${endpoints.target.y}`;
    }

    const deltaX = target.x - source.x;
    const deltaY = target.y - source.y;
    const length = Math.hypot(deltaX, deltaY) || 1;
    const bend = edge.feedback ? FEEDBACK_EDGE_BEND : BIDIRECTIONAL_EDGE_BEND;
    if (edge.reverse_edge_ids.length || edge.feedback) {
      const control = {
        x: (source.x + target.x) / 2 - (deltaY / length) * bend,
        y: (source.y + target.y) / 2 + (deltaX / length) * bend,
      };
      return `M ${endpoints.source.x} ${endpoints.source.y} Q ${control.x} ${control.y} ${endpoints.target.x} ${endpoints.target.y}`;
    }
    return `M ${endpoints.source.x} ${endpoints.source.y} L ${endpoints.target.x} ${endpoints.target.y}`;
  }

  edgeLabelPoint(edge: CaseFlowAgentCanvasEdgeProjection): Point {
    const source = this.nodeCenter(edge.source_step_id);
    const target = this.nodeCenter(edge.target_step_id);
    if (!source || !target) return { x: 0, y: 0 };
    if (edge.loop) return { x: source.x, y: source.y - this.nodeHeight };
    const deltaX = target.x - source.x;
    const deltaY = target.y - source.y;
    const length = Math.hypot(deltaX, deltaY) || 1;
    const bend = edge.feedback
      ? FEEDBACK_EDGE_BEND / 2
      : edge.reverse_edge_ids.length ? BIDIRECTIONAL_EDGE_BEND / 2 : 0;
    return {
      x: (source.x + target.x) / 2 - (deltaY / length) * bend,
      y: (source.y + target.y) / 2 + (deltaX / length) * bend - 8,
    };
  }

  private zoomBy(factor: number, anchor?: Point): void {
    const bounds = this.surfaceBounds();
    const focus = anchor ?? {
      x: (bounds.width || FALLBACK_SURFACE_WIDTH) / 2,
      y: (bounds.height || FALLBACK_SURFACE_HEIGHT) / 2,
    };
    const scale = clamp(this.viewport.scale * factor, MIN_SCALE, MAX_SCALE);
    const ratio = scale / this.viewport.scale;
    this.viewport = {
      scale,
      x: focus.x - (focus.x - this.viewport.x) * ratio,
      y: focus.y - (focus.y - this.viewport.y) * ratio,
    };
  }

  private refreshProjection(): void {
    const result = projectAgentCanvas(this.graph);
    if (result.ok) {
      this.projection = result.value;
      this.projectionError = '';
      return;
    }
    this.projection = null;
    this.projectionError = result.issues.map(issue => issue.message).join(' ');
  }

  private refreshRuntimeProjection(): void {
    this.runtimeProjection = projectCaseFlowAgentRuntime(
      this.graph.id,
      this.projection?.nodes ?? [],
      this.runtimeOverlay,
    );
  }

  private refreshEdgeActivityProjection(): void {
    this.edgeActivityProjection = projectCaseFlowAgentEdgeActivity(
      this.graph.id,
      this.projection?.edges ?? [],
      this.edgeTraceReadModel,
      this.runtimeOverlay,
    );
  }

  private positionFor(node: CaseFlowAgentCanvasNodeProjection): StepPosition {
    return this.previewPositions.get(node.step_id) ?? node.position;
  }

  private nodeCenter(stepId: string): Point | null {
    const node = this.projection?.nodes.find(candidate => candidate.step_id === stepId);
    if (!node) return null;
    const position = this.positionFor(node);
    return {
      x: position.x + this.nodeWidth / 2,
      y: position.y + this.nodeHeight / 2,
    };
  }

  private surfaceBounds(): DOMRect {
    return this.surfaceRef.nativeElement.getBoundingClientRect();
  }

  private captureBaselinePositions(): void {
    this.baselinePositions.clear();
    for (const node of this.projection?.nodes ?? []) {
      this.baselinePositions.set(node.step_id, { ...node.position });
    }
  }

  private reconcileBaselinePositions(): void {
    const activeIds = new Set((this.projection?.nodes ?? []).map(node => node.step_id));
    for (const stepId of this.baselinePositions.keys()) {
      if (!activeIds.has(stepId)) this.baselinePositions.delete(stepId);
    }
    for (const node of this.projection?.nodes ?? []) {
      if (!this.baselinePositions.has(node.step_id)) {
        this.baselinePositions.set(node.step_id, { ...node.position });
      }
    }
  }

  private hasSelectableId(id: string): boolean {
    return Boolean(
      this.projection?.nodes.some(node => node.step_id === id)
      || this.projection?.edges.some(edge => edge.edge_id === id),
    );
  }

  private get hasTypedSelection(): boolean {
    return this.selectedNodeId !== undefined || this.selectedEdgeIdentity !== undefined;
  }
}

function isActivationKey(event: KeyboardEvent): boolean {
  return event.key === 'Enter' || event.key === ' ';
}

function keyboardNudgeDirection(key: string): Point | null {
  const directions: Readonly<Record<string, Point>> = {
    ArrowLeft: { x: -1, y: 0 },
    ArrowRight: { x: 1, y: 0 },
    ArrowUp: { x: 0, y: -1 },
    ArrowDown: { x: 0, y: 1 },
  };
  return directions[key] ?? null;
}

function capturePointer(event: PointerEvent): void {
  const target = event.currentTarget as Element | null;
  if (target?.setPointerCapture && event.pointerId !== undefined) {
    target.setPointerCapture(event.pointerId);
  }
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function rectangleEdgeEndpoints(source: Point, target: Point): { source: Point; target: Point } {
  const deltaX = target.x - source.x;
  const deltaY = target.y - source.y;
  const sourceScale = boundaryScale(deltaX, deltaY);
  const targetScale = boundaryScale(-deltaX, -deltaY);
  return {
    source: {
      x: source.x + deltaX * sourceScale,
      y: source.y + deltaY * sourceScale,
    },
    target: {
      x: target.x - deltaX * targetScale,
      y: target.y - deltaY * targetScale,
    },
  };
}

function boundaryScale(deltaX: number, deltaY: number): number {
  const horizontal = Math.abs(deltaX) / (CASEFLOW_AGENT_NODE_WIDTH / 2);
  const vertical = Math.abs(deltaY) / (CASEFLOW_AGENT_NODE_HEIGHT / 2);
  return 1 / Math.max(horizontal, vertical, 1);
}

function loopPath(center: Point): string {
  const startX = center.x + 30;
  const endX = center.x - 30;
  const nodeTop = center.y - CASEFLOW_AGENT_NODE_HEIGHT / 2;
  return [
    `M ${startX} ${nodeTop}`,
    `C ${center.x + CASEFLOW_AGENT_NODE_WIDTH / 2 + 58} ${nodeTop - 76}`,
    `${center.x - CASEFLOW_AGENT_NODE_WIDTH / 2 - 58} ${nodeTop - 76}`,
    `${endX} ${nodeTop}`,
  ].join(' ');
}
