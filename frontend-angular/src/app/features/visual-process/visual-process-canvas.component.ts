import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { VpCanvasInteractionService } from './vp-canvas-interaction.service';
import { VpEdge, VpGraph, VpRuntimeOverlay, VpStep } from './visual-process-api.service';
import { NODE_H, NODE_W, hintColor, nodeKindColor } from './vp-editor-config';

export type VisualProcessCanvasMode = 'compact-readonly' | 'embedded-edit' | 'full-editor';

@Component({
  selector: 'app-visual-process-canvas',
  standalone: true,
  template: `
    <div class="vpe-canvas-wrap" [class.readonly]="readOnly"
         (mousedown)="canvasMouseDown.emit($event)" (mousemove)="canvasMouseMove.emit($event)"
         (mouseup)="canvasMouseUp.emit($event)" (wheel)="canvasWheel.emit($event)">
      <svg class="vpe-svg" role="img" [attr.aria-label]="'Prozess ' + graph.name">
        <defs>
          <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="#aaa"/></marker>
          <marker id="arrowback" markerWidth="8" markerHeight="6" refX="0" refY="3" orient="auto"><polygon points="8 0, 0 3, 8 6" fill="#7f8c8d" fill-opacity="0.8"/></marker>
        </defs>
        <g [attr.transform]="canvasTransform">
          @for (edge of graph.edges; track edge.id) {
            <g (click)="edgeSelected.emit(edge.id)" class="vpe-edge-g">
              <path [attr.d]="interaction.edgePath(edge, graph.steps)" [class.selected]="selectedId === edge.id"
                    [class.back-edge]="edge.condition.kind === 'back_edge'" class="vpe-edge"
                    [attr.marker-end]="edge.condition.kind === 'back_edge' ? '' : 'url(#arrowhead)'"
                    [attr.marker-start]="edge.condition.kind === 'back_edge' ? 'url(#arrowback)' : ''"/>
              @if (edge.label) { <text [attr.x]="mid(edge).x" [attr.y]="mid(edge).y - 4" class="vpe-edge-label">{{ edge.label }}</text> }
            </g>
          }
          @if (drawingEdge && !readOnly) { <path [attr.d]="interaction.liveEdgePath(graph.steps, edgeSourceId)" class="vpe-edge live"/> }
          @for (step of graph.steps; track step.id) {
            <g [attr.transform]="'translate(' + step.position.x + ',' + step.position.y + ')'"
               (mousedown)="nodeDown($event, step.id)" (click)="stepSelected.emit(step.id)" class="vpe-node-g"
               [class.selected]="selectedId === step.id" [class.edge-source]="edgeSourceId === step.id"
               [class.awaiting-gate]="state(step.id) === 'awaiting_approval'" [attr.data-run-state]="state(step.id)">
              @if (step.kind === 'fork' || step.kind === 'join' || step.kind === 'parallel') {
                <polygon [attr.points]="interaction.diamondPoints()" [attr.fill]="nodeColor(step)" class="vpe-node-rect vpe-diamond"/>
              } @else { <rect [attr.width]="NODE_W" [attr.height]="NODE_H" rx="7" [attr.fill]="nodeColor(step)" class="vpe-node-rect"/> }
              @if (step.gate) { <text x="4" y="14" class="vpe-node-icon">🔒</text> }
              <text [attr.x]="NODE_W / 2" y="22" class="vpe-node-label">{{ step.label }}</text>
              <text [attr.x]="NODE_W / 2" y="38" class="vpe-node-kind">{{ step.kind }}</text>
              @if (state(step.id); as runState) {
                <circle [attr.cx]="NODE_W - 10" cy="10" r="5" [attr.fill]="runStateColor(runState)"/>
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
  styles: [':host{display:contents}'],
})
export class VisualProcessCanvasComponent {
  readonly interaction = inject(VpCanvasInteractionService);
  readonly NODE_W = NODE_W; readonly NODE_H = NODE_H;
  @Input({ required: true }) graph!: VpGraph;
  @Input() runtimeOverlay: VpRuntimeOverlay | null = null;
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
  nodeDown(event: MouseEvent, stepId: string): void { if (!this.readOnly) this.nodeMouseDown.emit({ event, stepId }); }
  mid(edge: VpEdge): { x: number; y: number } { return this.interaction.edgeMidpoint(edge, this.graph.steps); }
  state(stepId: string): string { return this.runtimeOverlay?.steps[stepId]?.status ?? ''; }
  nodeColor(step: VpStep): string { return step.policy_hints?.length ? hintColor(step.policy_hints) : nodeKindColor(step.kind); }
  runStateColor(state: string): string { return ({ pending:'#7f8c8d', running:'#3498db', awaiting_approval:'#f39c12', succeeded:'#2ecc71', done:'#2ecc71', failed:'#e74c3c', skipped:'#95a5a6', cancelled:'#8e44ad' } as Record<string,string>)[state] ?? '#7f8c8d'; }
  stateIcon(state: string): string { return ({ pending:'○', running:'▶', awaiting_approval:'◆', succeeded:'✓', done:'✓', failed:'✕', skipped:'↷', cancelled:'■' } as Record<string,string>)[state] ?? '?'; }
}
