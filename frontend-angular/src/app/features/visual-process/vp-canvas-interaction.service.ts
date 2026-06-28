import { Injectable, computed, signal } from '@angular/core';
import { VpEdge, VpStep } from './visual-process-api.service';

const NODE_W = 140;
const NODE_H = 52;

@Injectable()
export class VpCanvasInteractionService {

  // ── Pan/Zoom State ─────────────────────────────────────────────────────────
  private _panX = signal(20);
  private _panY = signal(20);
  private _zoom = signal(1);
  private isPanning = false;
  private panStart = { x: 0, y: 0 };
  private panStartOrigin = { x: 0, y: 0 };

  // ── Drag State ─────────────────────────────────────────────────────────────
  private dragId: string | null = null;
  private dragOffset = { x: 0, y: 0 };

  // ── Live Edge Drawing ──────────────────────────────────────────────────────
  drawingEdge = signal<boolean>(false);
  private mouseSvg = { x: 0, y: 0 };

  // ── Computed ───────────────────────────────────────────────────────────────
  canvasTransform = computed(
    () => `translate(${this._panX()}, ${this._panY()}) scale(${this._zoom()})`,
  );

  // ── Mouse / Wheel Handlers ─────────────────────────────────────────────────
  onCanvasMouseDown(e: MouseEvent, onClearSelection: () => void): void {
    if (e.altKey) {
      this.isPanning = true;
      this.panStart = { x: e.clientX, y: e.clientY };
      this.panStartOrigin = { x: this._panX(), y: this._panY() };
      return;
    }
    if (
      (e.target as SVGElement).closest('.vpe-node-g') ||
      (e.target as SVGElement).closest('.vpe-edge-g')
    ) return;
    onClearSelection();
  }

  onMouseMove(
    e: MouseEvent,
    mutateStepFn: (id: string, fn: (s: VpStep) => void) => void,
  ): void {
    if (this.isPanning) {
      this._panX.set(this.panStartOrigin.x + (e.clientX - this.panStart.x));
      this._panY.set(this.panStartOrigin.y + (e.clientY - this.panStart.y));
      return;
    }
    if (this.dragId) {
      const { svgX, svgY } = this.clientToSvg(e.clientX, e.clientY);
      const id = this.dragId;
      const ox = this.dragOffset.x;
      const oy = this.dragOffset.y;
      mutateStepFn(id, s => {
        s.position.x = svgX - ox;
        s.position.y = svgY - oy;
      });
      return;
    }
    if (this.drawingEdge()) {
      const { svgX, svgY } = this.clientToSvg(e.clientX, e.clientY);
      this.mouseSvg = { x: svgX, y: svgY };
    }
  }

  onMouseUp(_e: MouseEvent): void {
    this.isPanning = false;
    this.dragId = null;
  }

  onWheel(e: WheelEvent): void {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.1 : 0.91;
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    this._panX.set(cx - factor * (cx - this._panX()));
    this._panY.set(cy - factor * (cy - this._panY()));
    this._zoom.set(Math.min(4, Math.max(0.2, this._zoom() * factor)));
  }

  onNodeMouseDown(e: MouseEvent, id: string, step: VpStep, isEdgeMode: boolean): void {
    e.stopPropagation();
    if (isEdgeMode) return;
    const { svgX, svgY } = this.clientToSvg(e.clientX, e.clientY);
    this.dragId = id;
    this.dragOffset = { x: svgX - step.position.x, y: svgY - step.position.y };
  }

  // ── SVG Coordinate Helpers ─────────────────────────────────────────────────
  clientToSvg(cx: number, cy: number): { svgX: number; svgY: number } {
    const wrap = document.querySelector('.vpe-canvas-wrap');
    if (!wrap) return { svgX: cx, svgY: cy };
    const rect = wrap.getBoundingClientRect();
    return {
      svgX: (cx - rect.left - this._panX()) / this._zoom(),
      svgY: (cy - rect.top  - this._panY()) / this._zoom(),
    };
  }

  edgePath(edge: VpEdge, steps: VpStep[]): string {
    const src = steps.find(s => s.id === edge.source);
    const tgt = steps.find(s => s.id === edge.target);
    if (!src || !tgt) return '';
    return this.bezierPath(
      src.position.x + NODE_W, src.position.y + NODE_H / 2,
      tgt.position.x, tgt.position.y + NODE_H / 2,
      edge.condition.kind === 'back_edge',
    );
  }

  edgeMidpoint(edge: VpEdge, steps: VpStep[]): { x: number; y: number } {
    const src = steps.find(s => s.id === edge.source);
    const tgt = steps.find(s => s.id === edge.target);
    if (!src || !tgt) return { x: 0, y: 0 };
    return {
      x: (src.position.x + NODE_W + tgt.position.x) / 2,
      y: (src.position.y + tgt.position.y) / 2 + NODE_H / 2,
    };
  }

  liveEdgePath(steps: VpStep[], sourceId: string | null): string {
    const src = steps.find(s => s.id === sourceId);
    if (!src) return '';
    return this.bezierPath(
      src.position.x + NODE_W, src.position.y + NODE_H / 2,
      this.mouseSvg.x, this.mouseSvg.y,
      false,
    );
  }

  private bezierPath(
    x1: number, y1: number, x2: number, y2: number, isBack: boolean,
  ): string {
    const dx = Math.abs(x2 - x1) * 0.5 || 60;
    if (isBack) {
      const cy = Math.max(y1, y2) + 60;
      return `M ${x1} ${y1} C ${x1} ${cy}, ${x2} ${cy}, ${x2} ${y2}`;
    }
    return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
  }

  diamondPoints(): string {
    const cx = NODE_W / 2;
    const cy = NODE_H / 2;
    const rx = NODE_W / 2 - 2;
    const ry = NODE_H / 2 - 2;
    return `${cx},${cy - ry} ${cx + rx},${cy} ${cx},${cy + ry} ${cx - rx},${cy}`;
  }
}
