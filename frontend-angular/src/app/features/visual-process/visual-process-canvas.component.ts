import {
  AfterViewInit,
  Component,
  ElementRef,
  EventEmitter,
  Input,
  OnDestroy,
  Output,
  ViewChild,
  inject,
} from '@angular/core';

import { CanvasHitTarget } from './vp-editor-context.models';
import { VpCanvasInteractionService } from './vp-canvas-interaction.service';
import { ValidationIssue, VpEdge, VpGraph, VpRuntimeOverlay, VpStep } from './visual-process-api.service';
import { NODE_H, NODE_W, hintColor, nodeKindColor } from './vp-editor-config';

export type VisualProcessCanvasMode = 'compact-readonly' | 'embedded-edit' | 'full-editor';

let canvasSequence = 0;

@Component({
  selector: 'app-visual-process-canvas',
  standalone: true,
  template: `
    <div #canvasWrap class="vpe-canvas-wrap" [class.readonly]="readOnly" role="region" tabindex="0"
         [attr.aria-label]="'Visueller Prozess ' + graph.name"
         data-semantic-kind="canvas_region" [attr.data-entity-id]="graph.id"
         (pointerdown)="canvasMouseDown.emit($event)" (pointermove)="canvasMouseMove.emit($event)"
         (pointerup)="pointerUp($event)" (pointercancel)="pointerUp($event)"
         (pointerleave)="targetPreviewed.emit(null)" (click)="canvasClick($event)" (wheel)="canvasWheel.emit($event)"
         (keydown)="canvasKeydown($event)">
      <svg class="vpe-svg" role="group" [attr.aria-label]="'Prozessgraph ' + graph.name">
        <defs>
          <marker [id]="arrowheadId" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="#aaa"/></marker>
          <marker [id]="arrowbackId" markerWidth="8" markerHeight="6" refX="0" refY="3" orient="auto"><polygon points="8 0, 0 3, 8 6" fill="#7f8c8d" fill-opacity="0.8"/></marker>
        </defs>
        <g [attr.transform]="canvasTransform">
          @for (edge of graph.edges; track edge.id) {
            <g (click)="selectEdge(edge)" (keydown)="edgeKeydown($event, edge)"
               (pointerenter)="targetPreviewed.emit(edgeTarget(edge))" (pointerleave)="targetPreviewed.emit(null)"
               (focus)="targetFocused.emit(edgeTarget(edge))"
               class="vpe-edge-g" tabindex="0" role="button"
               [attr.aria-label]="edgeAriaLabel(edge)" [attr.aria-selected]="selectedId === edge.id"
               data-semantic-kind="edge" [attr.data-entity-id]="edge.id">
              <path [attr.d]="interaction.edgePath(edge, graph.steps)" [class.selected]="selectedId === edge.id"
                    [class.back-edge]="edge.condition.kind === 'back_edge'" class="vpe-edge"
                    [attr.marker-end]="edge.condition.kind === 'back_edge' ? '' : markerUrl(arrowheadId)"
                    [attr.marker-start]="edge.condition.kind === 'back_edge' ? markerUrl(arrowbackId) : ''"/>
              @if (edge.label || edge.condition.kind !== 'always') {
                <text [attr.x]="mid(edge).x" [attr.y]="mid(edge).y - 4" class="vpe-edge-label"
                      tabindex="0" role="button" data-semantic-kind="edge_condition"
                      [attr.aria-label]="'Kantenbedingung ' + edge.condition.kind"
                      (pointerenter)="targetPreviewed.emit(edgeConditionTarget(edge))"
                      (pointerleave)="targetPreviewed.emit(edgeTarget(edge))"
                      (focus)="targetFocused.emit(edgeConditionTarget(edge))"
                      (click)="selectTarget($event, edgeConditionTarget(edge))"
                      (keydown)="targetKeydown($event, edgeConditionTarget(edge))">{{ edge.label || edge.condition.kind }}</text>
              }
            </g>
          }
          @if (drawingEdge && !readOnly) { <path [attr.d]="interaction.liveEdgePath(graph.steps, edgeSourceId)" class="vpe-edge live"/> }
          @for (step of graph.steps; track step.id) {
            <g [attr.transform]="'translate(' + step.position.x + ',' + step.position.y + ')'"
               (pointerdown)="nodeDown($event, step.id)" (click)="selectStep(step)"
               (pointerenter)="targetPreviewed.emit(stepTarget(step))" (pointerleave)="targetPreviewed.emit(null)"
               (focus)="targetFocused.emit(stepTarget(step))"
               (keydown)="stepKeydown($event, step)" class="vpe-node-g" tabindex="0" role="button"
               [attr.aria-label]="stepAriaLabel(step)" [attr.aria-selected]="selectedId === step.id"
               [class.selected]="selectedId === step.id" [class.edge-source]="edgeSourceId === step.id"
               [class.awaiting-gate]="state(step.id) === 'awaiting_approval'" [attr.data-run-state]="state(step.id)"
               data-semantic-kind="node" [attr.data-entity-id]="step.id">
              @if (step.kind === 'fork' || step.kind === 'join' || step.kind === 'parallel') {
                <polygon [attr.points]="interaction.diamondPoints()" [attr.fill]="nodeColor(step)" class="vpe-node-rect vpe-diamond"/>
              } @else { <rect [attr.width]="NODE_W" [attr.height]="NODE_H" rx="7" [attr.fill]="nodeColor(step)" class="vpe-node-rect"/> }
              @if (step.gate) { <text x="4" y="14" class="vpe-node-icon" aria-hidden="true">🔒</text> }
              <text [attr.x]="NODE_W / 2" y="22" class="vpe-node-label">{{ step.label }}</text>
              <text [attr.x]="NODE_W / 2" y="38" class="vpe-node-kind">{{ step.kind }}</text>

              @for (port of step.io.inputs; track port.name || $index) {
                <circle class="vpe-node-port input" cx="0" [attr.cy]="portY($index, step.io.inputs.length)" r="5"
                        tabindex="0" role="button" data-semantic-kind="node_port"
                        [attr.aria-label]="'Input ' + (port.name || $index) + ' von ' + step.label"
                        (pointerenter)="targetPreviewed.emit(portTarget(step, 'input', port.name, $index))"
                        (pointerleave)="targetPreviewed.emit(stepTarget(step))"
                        (focus)="targetFocused.emit(portTarget(step, 'input', port.name, $index))"
                        (click)="selectTarget($event, portTarget(step, 'input', port.name, $index))"
                        (keydown)="targetKeydown($event, portTarget(step, 'input', port.name, $index))" />
              }
              @for (port of step.io.outputs; track port.name || $index) {
                <circle class="vpe-node-port output" [attr.cx]="NODE_W" [attr.cy]="portY($index, step.io.outputs.length)" r="5"
                        tabindex="0" role="button" data-semantic-kind="node_port"
                        [attr.aria-label]="'Output ' + (port.name || $index) + ' von ' + step.label"
                        (pointerenter)="targetPreviewed.emit(portTarget(step, 'output', port.name, $index))"
                        (pointerleave)="targetPreviewed.emit(stepTarget(step))"
                        (focus)="targetFocused.emit(portTarget(step, 'output', port.name, $index))"
                        (click)="selectTarget($event, portTarget(step, 'output', port.name, $index))"
                        (keydown)="targetKeydown($event, portTarget(step, 'output', port.name, $index))" />
              }

              @if (issuesForStep(step.id).length; as issueCount) {
                <g class="vpe-node-badge validation" tabindex="0" role="button" data-semantic-kind="validation_badge"
                   [attr.aria-label]="issueCount + ' Validierungsprobleme bei ' + step.label"
                   (pointerenter)="targetPreviewed.emit(validationTarget(step))"
                   (pointerleave)="targetPreviewed.emit(stepTarget(step))"
                   (focus)="targetFocused.emit(validationTarget(step))"
                   (click)="selectTarget($event, validationTarget(step))"
                   (keydown)="targetKeydown($event, validationTarget(step))">
                  <circle cx="10" [attr.cy]="NODE_H - 8" r="7"/><text x="10" [attr.y]="NODE_H - 5">!</text>
                </g>
              }
              @if (state(step.id); as runState) {
                <g class="vpe-node-badge runtime" tabindex="0" role="button" data-semantic-kind="runtime_badge"
                   [attr.aria-label]="'Runtime-Status ' + runState + ' bei ' + step.label"
                   (pointerenter)="targetPreviewed.emit(runtimeTarget(step))"
                   (pointerleave)="targetPreviewed.emit(stepTarget(step))"
                   (focus)="targetFocused.emit(runtimeTarget(step))"
                   (click)="selectTarget($event, runtimeTarget(step))"
                   (keydown)="targetKeydown($event, runtimeTarget(step))">
                  <circle [attr.cx]="NODE_W - 10" cy="10" r="5" [attr.fill]="runStateColor(runState)"/>
                </g>
                <text x="6" [attr.y]="NODE_H - 8" class="vpe-node-kind">{{ stateIcon(runState) }} {{ runState }}</text>
              }
            </g>
          }
        </g>
      </svg>
      @if (graph.steps.length === 0) { <div class="vpe-empty-hint">Kein Prozess konfiguriert.</div> }
    </div>
  `,
  styleUrls: ['./visual-process-editor.component.scss'],
  styles: [`
    :host{display:contents}.vpe-node-port{fill:#dce8f8;stroke:#102036;stroke-width:2;cursor:help}.vpe-node-port:focus{stroke:#ffd166;stroke-width:3;outline:none}
    .vpe-node-badge{cursor:help}.vpe-node-badge:focus{outline:none;filter:drop-shadow(0 0 3px #ffd166)}.vpe-node-badge.validation circle{fill:#e74c3c}.vpe-node-badge.validation text{fill:#fff;text-anchor:middle;font-size:10px;font-weight:bold}
    .vpe-node-g:focus-visible .vpe-node-rect,.vpe-edge-g:focus-visible .vpe-edge{stroke:#ffd166!important;stroke-width:3px!important}
  `],
})
export class VisualProcessCanvasComponent implements AfterViewInit, OnDestroy {
  readonly interaction = inject(VpCanvasInteractionService);
  readonly NODE_W = NODE_W;
  readonly NODE_H = NODE_H;
  readonly canvasInstanceId = `vp-canvas-${++canvasSequence}`;
  readonly arrowheadId = `${this.canvasInstanceId}-arrowhead`;
  readonly arrowbackId = `${this.canvasInstanceId}-arrowback`;

  @ViewChild('canvasWrap', { static: true }) canvasWrap!: ElementRef<HTMLElement>;
  @Input({ required: true }) graph!: VpGraph;
  @Input() runtimeOverlay: VpRuntimeOverlay | null = null;
  @Input() validationIssues: readonly ValidationIssue[] = [];
  @Input() selectedId: string | null = null;
  @Input() readOnly = false;
  @Input() mode: VisualProcessCanvasMode = 'full-editor';
  @Input() canvasTransform = '';
  @Input() drawingEdge = false;
  @Input() edgeSourceId: string | null = null;
  @Output() stepSelected = new EventEmitter<string>();
  @Output() edgeSelected = new EventEmitter<string>();
  @Output() nodeMouseDown = new EventEmitter<{ event: MouseEvent; stepId: string }>();
  @Output() canvasMouseDown = new EventEmitter<MouseEvent>();
  @Output() canvasMouseMove = new EventEmitter<MouseEvent>();
  @Output() canvasMouseUp = new EventEmitter<MouseEvent>();
  @Output() canvasWheel = new EventEmitter<WheelEvent>();
  @Output() targetPreviewed = new EventEmitter<CanvasHitTarget | null>();
  @Output() targetFocused = new EventEmitter<CanvasHitTarget>();
  @Output() targetSelected = new EventEmitter<CanvasHitTarget>();

  ngAfterViewInit(): void {
    this.interaction.bindCanvas(this.canvasWrap.nativeElement);
  }

  ngOnDestroy(): void {
    this.interaction.unbindCanvas(this.canvasWrap.nativeElement);
  }

  nodeDown(event: MouseEvent, stepId: string): void {
    if (!this.readOnly) this.nodeMouseDown.emit({ event, stepId });
  }

  pointerUp(event: PointerEvent): void {
    this.canvasMouseUp.emit(event);
    if (event.type === 'pointercancel' || event.pointerType === 'touch') {
      this.targetPreviewed.emit(null);
    }
  }

  selectStep(step: VpStep): void {
    this.stepSelected.emit(step.id);
    this.targetSelected.emit(this.stepTarget(step));
  }

  selectEdge(edge: VpEdge): void {
    this.edgeSelected.emit(edge.id);
    this.targetSelected.emit(this.edgeTarget(edge));
  }

  stepKeydown(event: KeyboardEvent, step: VpStep): void {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    this.selectStep(step);
  }

  edgeKeydown(event: KeyboardEvent, edge: VpEdge): void {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    this.selectEdge(edge);
  }

  targetKeydown(event: KeyboardEvent, target: CanvasHitTarget): void {
    if (!['Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    this.targetSelected.emit(target);
  }

  selectTarget(event: MouseEvent, target: CanvasHitTarget): void {
    event.preventDefault();
    event.stopPropagation();
    if (target.stepId) this.stepSelected.emit(target.stepId);
    else if (target.edgeId) this.edgeSelected.emit(target.edgeId);
    this.targetSelected.emit(target);
  }

  canvasClick(event: MouseEvent): void {
    if ((event.target as Element).closest('.vpe-node-g,.vpe-edge-g')) return;
    this.targetSelected.emit(this.canvasTarget());
  }

  canvasKeydown(event: KeyboardEvent): void {
    if (!['Enter', ' '].includes(event.key) || event.target !== this.canvasWrap.nativeElement) return;
    event.preventDefault();
    const target = this.canvasTarget();
    this.targetFocused.emit(target);
    this.targetSelected.emit(target);
  }

  markerUrl(id: string): string { return `url(#${id})`; }
  mid(edge: VpEdge): { x: number; y: number } { return this.interaction.edgeMidpoint(edge, this.graph.steps); }
  state(stepId: string): string { return this.runtimeOverlay?.steps[stepId]?.status ?? ''; }
  issuesForStep(stepId: string): readonly ValidationIssue[] { return this.validationIssues.filter(issue => issue.step_id === stepId); }
  nodeColor(step: VpStep): string { return step.policy_hints?.length ? hintColor(step.policy_hints) : nodeKindColor(step.kind); }
  runStateColor(state: string): string { return ({ pending:'#7f8c8d', running:'#3498db', awaiting_approval:'#f39c12', succeeded:'#2ecc71', done:'#2ecc71', failed:'#e74c3c', skipped:'#95a5a6', cancelled:'#8e44ad' } as Record<string,string>)[state] ?? '#7f8c8d'; }
  stateIcon(state: string): string { return ({ pending:'○', running:'▶', awaiting_approval:'◆', succeeded:'✓', done:'✓', failed:'✕', skipped:'↷', cancelled:'■' } as Record<string,string>)[state] ?? '?'; }
  portY(index: number, count: number): number { return ((index + 1) * NODE_H) / (count + 1); }

  stepAriaLabel(step: VpStep): string {
    const runtime = this.state(step.id);
    return `Node ${step.label}, Typ ${step.kind}${runtime ? `, Status ${runtime}` : ''}${step.gate ? ', Freigabe erforderlich' : ''}`;
  }

  edgeAriaLabel(edge: VpEdge): string {
    return `Kante ${edge.label || edge.id}, von ${edge.source} nach ${edge.target}, Bedingung ${edge.condition.kind}`;
  }

  stepTarget(step: VpStep): CanvasHitTarget { return { kind: 'node', entityId: step.id, graphId: this.graph.id, role: step.kind, stepId: step.id }; }
  edgeTarget(edge: VpEdge): CanvasHitTarget { return { kind: 'edge', entityId: edge.id, graphId: this.graph.id, role: 'transition', edgeId: edge.id }; }
  edgeConditionTarget(edge: VpEdge): CanvasHitTarget { return { kind: 'edge_condition', entityId: `${edge.id}:condition`, graphId: this.graph.id, role: edge.condition.kind, edgeId: edge.id }; }
  canvasTarget(): CanvasHitTarget { return { kind: 'canvas_region', entityId: this.graph.id, graphId: this.graph.id, role: 'workflow-canvas' }; }
  portTarget(step: VpStep, direction: 'input'|'output', name: string, index: number): CanvasHitTarget {
    const portName = name || `${direction}-${index}`;
    return { kind: 'node_port', entityId: `${step.id}:${direction}:${portName}`, graphId: this.graph.id, role: `${direction}-port`, stepId: step.id, portDirection: direction, portName };
  }
  validationTarget(step: VpStep): CanvasHitTarget { return { kind: 'validation_badge', entityId: `${step.id}:validation`, graphId: this.graph.id, role: 'validation-status', stepId: step.id }; }
  runtimeTarget(step: VpStep): CanvasHitTarget { return { kind: 'runtime_badge', entityId: `${step.id}:runtime`, graphId: this.graph.id, role: 'runtime-status', stepId: step.id }; }
}
