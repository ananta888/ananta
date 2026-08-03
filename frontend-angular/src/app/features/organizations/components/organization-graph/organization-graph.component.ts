import { CommonModule } from '@angular/common';
import { Component, computed, ElementRef, inject, signal, viewChild } from '@angular/core';

import {
  OrganizationLayoutPreference,
  OrganizationTopologyEdge,
  OrganizationTopologyNode,
} from '../../models/organization-topology.models';
import { OrganizationTopologyStateService } from '../../services/organization-topology-state.service';
import {
  CANVAS_VIEWPORT_PORT,
  CanvasPoint,
  CanvasViewport,
  DefaultCanvasViewportAdapter,
} from './canvas-viewport.port';

const CLIENT_NODE_LIMIT = 500;
const CLIENT_EDGE_LIMIT = 2_000;

interface PositionedNode extends OrganizationTopologyNode, CanvasPoint {}

@Component({
  selector: 'app-organization-graph',
  standalone: true,
  imports: [CommonModule],
  providers: [{ provide: CANVAS_VIEWPORT_PORT, useClass: DefaultCanvasViewportAdapter }],
  template: `
    <section class="graph-panel" aria-labelledby="organization-graph-heading">
      <header>
        <div>
          <p class="eyebrow">Read-only Topology Canvas</p>
          <h2 id="organization-graph-heading">Graph</h2>
        </div>
        <div class="toolbar" role="toolbar" aria-label="Graphansicht steuern">
          <button type="button" (click)="zoomBy(1.2)" aria-label="Vergrößern">＋</button>
          <button type="button" (click)="zoomBy(1 / 1.2)" aria-label="Verkleinern">−</button>
          <button type="button" (click)="fit()">Einpassen</button>
          <button type="button" (click)="autoLayout()">Auto-Layout</button>
          <button type="button" (click)="state.saveLayout()" [disabled]="!state.layoutPreferences().size">Layout speichern</button>
        </div>
      </header>

      @if (isClientTruncated()) {
        <p class="limit" role="status">Darstellung auf {{ CLIENT_NODE_LIMIT }} Knoten und {{ CLIENT_EDGE_LIMIT }} Kanten begrenzt. Nutze Filter oder Fokus-Subgraph.</p>
      }

      <div
        #canvas
        class="canvas"
        tabindex="0"
        role="application"
        aria-label="Organisationsgraph. Pfeiltasten verschieben, Plus und Minus zoomen."
        (wheel)="onWheel($event)"
        (pointerdown)="startPan($event)"
        (pointermove)="movePointer($event)"
        (pointerup)="endPointer($event)"
        (pointercancel)="endPointer($event)"
        (keydown)="onCanvasKeydown($event)">
        <div class="world" [style.transform]="worldTransform()">
          <svg class="edges" [attr.width]="worldSize.width" [attr.height]="worldSize.height" role="group" aria-label="Organisationskanten">
            <defs>
              <marker id="organization-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" fill="context-stroke" />
              </marker>
            </defs>
            @for (edge of renderEdges(); track edge.id) {
              @if (edgeLine(edge); as line) {
                <g
                  class="edge-control"
                  [class.selected]="state.selectedEdgeId() === edge.id"
                  role="button"
                  tabindex="0"
                  [attr.aria-label]="edgeLabel(edge)"
                  (click)="selectEdge($event, edge)"
                  (keydown.enter)="selectEdge($event, edge)"
                  (keydown.space)="selectEdge($event, edge)">
                  <line
                    [attr.x1]="line.x1" [attr.y1]="line.y1"
                    [attr.x2]="line.x2" [attr.y2]="line.y2"
                    [attr.class]="'edge ' + edge.namespace + ' ' + edge.kind"
                    marker-end="url(#organization-arrow)" />
                  <line
                    class="edge-hit"
                    [attr.x1]="line.x1" [attr.y1]="line.y1"
                    [attr.x2]="line.x2" [attr.y2]="line.y2" />
                </g>
              }
            }
          </svg>

          @for (node of positionedNodes(); track node.id) {
            <button
              type="button"
              class="node"
              [class.selected]="state.selectedNodeId() === node.id"
              [attr.data-kind]="node.kind"
              [style.left.px]="node.x"
              [style.top.px]="node.y"
              [attr.aria-label]="kindLabel(node.kind) + ' ' + node.label"
              (click)="selectNode($event, node)"
              (dblclick)="focusNode($event, node)"
              (pointerdown)="startNodeDrag($event, node)">
              <span class="node-kind">{{ kindLabel(node.kind) }}</span>
              <strong>{{ node.label }}</strong>
              @if (runtimeLabel(node.id); as status) { <small>{{ status }}</small> }
            </button>
          }
        </div>
      </div>

      <footer>
        <span>Zoom {{ (viewport().zoom * 100).toFixed(0) }} %</span>
        <span><i class="legend hierarchy"></i> Hierarchie</span>
        <span><i class="legend organization"></i> Organisation</span>
        <span><i class="legend runtime"></i> Runtime (read-only)</span>
      </footer>
    </section>
  `,
  styles: [`
    .graph-panel { display: grid; gap: .7rem; min-width: 0; }
    header { align-items: end; display: flex; gap: 1rem; justify-content: space-between; }
    h2, p { margin: 0; } .eyebrow { color: #76a9ff; font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    .toolbar { display: flex; flex-wrap: wrap; gap: .35rem; justify-content: flex-end; }
    button { background: #223552; border: 1px solid #496083; border-radius: .4rem; color: #edf4ff; cursor: pointer; padding: .4rem .6rem; }
    button:hover { background: #2c456c; } button:focus-visible { outline: 3px solid #79aef7; outline-offset: 2px; } button:disabled { opacity: .45; }
    .canvas { background-color: #07101e; background-image: radial-gradient(#263d60 1px, transparent 1px); background-size: 24px 24px; border: 1px solid #2e4264; border-radius: .7rem; height: min(68vh, 780px); min-height: 460px; overflow: hidden; position: relative; touch-action: none; }
    .world { height: 2400px; left: 0; position: absolute; top: 0; transform-origin: 0 0; width: 3200px; }
    .edges { inset: 0; overflow: visible; position: absolute; }
    .edge { fill: none; stroke: #7086aa; stroke-width: 2; }
    .edge-hit { fill: none; pointer-events: stroke; stroke: transparent; stroke-width: 14; }
    .edge-control { cursor: pointer; }
    .edge-control:focus .edge, .edge-control.selected .edge { filter: drop-shadow(0 0 3px #f8c65c); stroke: #f8c65c; stroke-width: 4; }
    .edge.organization { stroke: #69b8e8; stroke-dasharray: 8 5; }
    .edge.runtime { stroke: #e88e69; stroke-dasharray: 3 5; }
    .edge.handoff { stroke: #c890f0; }
    .node { align-items: flex-start; background: #13223b; border-color: #49668f; display: flex; flex-direction: column; gap: .15rem; height: 68px; justify-content: center; padding: .45rem .6rem; position: absolute; text-align: left; transform: translate(-50%, -50%); width: 160px; }
    .node[data-kind='organization'] { background: #253c67; border-color: #78a7ef; }
    .node[data-kind='team'] { background: #143d42; border-color: #45a7a0; }
    .node[data-kind='role_slot'] { background: #382c51; border-color: #9873c5; }
    .node[data-kind='assignment'] { background: #3b3222; border-color: #b39757; }
    .node.selected { box-shadow: 0 0 0 3px #f8c65c; }
    .node strong { max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .node-kind, .node small { color: #a6b8d4; font-size: .64rem; text-transform: uppercase; }
    .limit { background: #3a2d15; border-left: 4px solid #e4ab45; color: #ffdda0; padding: .5rem .7rem; }
    footer { color: #a4b3cd; display: flex; flex-wrap: wrap; gap: 1rem; font-size: .75rem; }
    .legend { border-top: 3px solid #7086aa; display: inline-block; margin-right: .25rem; width: 1.4rem; }
    .legend.organization { border-color: #69b8e8; border-top-style: dashed; }
    .legend.runtime { border-color: #e88e69; border-top-style: dotted; }
  `],
})
export class OrganizationGraphComponent {
  readonly CLIENT_NODE_LIMIT = CLIENT_NODE_LIMIT;
  readonly CLIENT_EDGE_LIMIT = CLIENT_EDGE_LIMIT;
  readonly worldSize = { width: 3200, height: 2400 };
  readonly state = inject(OrganizationTopologyStateService);
  private readonly viewportAdapter = inject(CANVAS_VIEWPORT_PORT);
  private readonly canvas = viewChild<ElementRef<HTMLDivElement>>('canvas');
  readonly viewport = signal<CanvasViewport>({ pan: { x: 80, y: 80 }, zoom: .85, width: 1000, height: 650 });
  readonly positionedNodes = computed<readonly PositionedNode[]>(() => {
    const nodes = this.state.visibleNodes().slice(0, CLIENT_NODE_LIMIT);
    const layout = this.state.layoutPreferences();
    const byDepth = new Map<number, OrganizationTopologyNode[]>();
    nodes.forEach(node => byDepth.set(node.depth, [...(byDepth.get(node.depth) ?? []), node]));
    return nodes.map(node => {
      const stored = layout.get(node.id);
      if (stored) return { ...node, x: stored.x, y: stored.y };
      const siblings = byDepth.get(node.depth) ?? [node];
      const index = siblings.findIndex(item => item.id === node.id);
      return {
        ...node,
        x: 180 + index * Math.max(190, Math.min(260, 2600 / Math.max(1, siblings.length))),
        y: 130 + node.depth * 180,
      };
    });
  });
  readonly renderEdges = computed(() => this.state.visibleEdges().slice(0, CLIENT_EDGE_LIMIT));
  readonly worldTransform = computed(() => {
    const viewport = this.viewport();
    return `translate(${viewport.pan.x}px, ${viewport.pan.y}px) scale(${viewport.zoom})`;
  });
  readonly isClientTruncated = computed(() => (
    this.state.visibleNodes().length > CLIENT_NODE_LIMIT || this.state.visibleEdges().length > CLIENT_EDGE_LIMIT
  ));

  private panStart: { pointerId: number; x: number; y: number; pan: CanvasPoint } | null = null;
  private nodeDrag: { pointerId: number; nodeId: string; start: CanvasPoint; origin: CanvasPoint } | null = null;

  onWheel(event: WheelEvent): void {
    event.preventDefault();
    const rect = this.canvas()?.nativeElement.getBoundingClientRect();
    if (!rect) return;
    this.viewport.update(viewport => this.viewportAdapter.zoomAt(
      viewport,
      { x: event.clientX - rect.left, y: event.clientY - rect.top },
      event.deltaY < 0 ? 1.12 : 1 / 1.12,
    ));
  }

  zoomBy(factor: number): void {
    const element = this.canvas()?.nativeElement;
    const width = element?.clientWidth ?? this.viewport().width;
    const height = element?.clientHeight ?? this.viewport().height;
    this.viewport.update(viewport => this.viewportAdapter.zoomAt(viewport, { x: width / 2, y: height / 2 }, factor));
  }

  fit(): void {
    const element = this.canvas()?.nativeElement;
    if (!element) return;
    this.viewport.set(this.viewportAdapter.fit(this.positionedNodes(), element.clientWidth, element.clientHeight));
  }

  autoLayout(): void {
    this.state.layoutPreferences.set(new Map());
    queueMicrotask(() => this.fit());
  }

  startPan(event: PointerEvent): void {
    if (event.button !== 0 || (event.target as HTMLElement).closest('.node')) return;
    const current = this.viewport();
    this.panStart = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, pan: current.pan };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }

  startNodeDrag(event: PointerEvent, node: PositionedNode): void {
    if (event.button !== 0) return;
    event.stopPropagation();
    this.nodeDrag = {
      pointerId: event.pointerId,
      nodeId: node.id,
      start: { x: event.clientX, y: event.clientY },
      origin: { x: node.x, y: node.y },
    };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }

  movePointer(event: PointerEvent): void {
    if (this.nodeDrag?.pointerId === event.pointerId) {
      const zoom = this.viewport().zoom;
      const x = this.nodeDrag.origin.x + (event.clientX - this.nodeDrag.start.x) / zoom;
      const y = this.nodeDrag.origin.y + (event.clientY - this.nodeDrag.start.y) / zoom;
      this.state.updateLayout({ node_id: this.nodeDrag.nodeId, x, y });
      return;
    }
    if (this.panStart?.pointerId === event.pointerId) {
      this.viewport.update(viewport => ({
        ...viewport,
        pan: {
          x: this.panStart!.pan.x + event.clientX - this.panStart!.x,
          y: this.panStart!.pan.y + event.clientY - this.panStart!.y,
        },
      }));
    }
  }

  endPointer(event: PointerEvent): void {
    if (this.nodeDrag?.pointerId === event.pointerId) this.nodeDrag = null;
    if (this.panStart?.pointerId === event.pointerId) this.panStart = null;
  }

  onCanvasKeydown(event: KeyboardEvent): void {
    const distance = event.shiftKey ? 80 : 24;
    const directions: Record<string, CanvasPoint> = {
      ArrowLeft: { x: distance, y: 0 },
      ArrowRight: { x: -distance, y: 0 },
      ArrowUp: { x: 0, y: distance },
      ArrowDown: { x: 0, y: -distance },
    };
    const direction = directions[event.key];
    if (direction) {
      event.preventDefault();
      this.viewport.update(viewport => ({
        ...viewport,
        pan: { x: viewport.pan.x + direction.x, y: viewport.pan.y + direction.y },
      }));
    } else if (event.key === '+' || event.key === '=') {
      event.preventDefault(); this.zoomBy(1.2);
    } else if (event.key === '-') {
      event.preventDefault(); this.zoomBy(1 / 1.2);
    }
  }

  selectNode(event: Event, node: OrganizationTopologyNode): void {
    event.stopPropagation();
    this.state.selectNode(node.id);
  }

  selectEdge(event: Event, edge: OrganizationTopologyEdge): void {
    event.preventDefault();
    event.stopPropagation();
    this.state.selectEdge(edge.id);
  }

  focusNode(event: Event, node: OrganizationTopologyNode): void {
    event.stopPropagation();
    this.state.setFocus(node.id);
  }

  edgeLine(edge: OrganizationTopologyEdge): { x1: number; y1: number; x2: number; y2: number } | null {
    const source = this.positionedNodes().find(node => node.id === edge.source_id);
    const target = this.positionedNodes().find(node => node.id === edge.target_id);
    return source && target ? { x1: source.x, y1: source.y, x2: target.x, y2: target.y } : null;
  }

  edgeLabel(edge: OrganizationTopologyEdge): string {
    const source = this.positionedNodes().find(node => node.id === edge.source_id);
    const target = this.positionedNodes().find(node => node.id === edge.target_id);
    return `${edge.namespace} ${edge.kind}: ${source?.label ?? edge.source_id} → ${target?.label ?? edge.target_id}`;
  }

  runtimeLabel(nodeId: string): string {
    return this.state.topology()?.runtime_overlay?.nodes.find(item => item.node_id === nodeId)?.status.label ?? '';
  }

  kindLabel(kind: OrganizationTopologyNode['kind']): string {
    return ({
      organization: 'Organisation', coordination_unit: 'Koordination', value_stream: 'Value Stream',
      team: 'Team', role_slot: 'Rolle', assignment: 'Zuweisung',
    } as const)[kind];
  }
}
