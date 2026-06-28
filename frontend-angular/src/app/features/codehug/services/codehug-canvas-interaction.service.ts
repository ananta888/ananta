import { Injectable, computed, signal } from '@angular/core';

import { CanvasNode } from '../components/codehug-canvas-types';

@Injectable()
export class CodehugCanvasInteractionService {
  readonly viewTx = signal(40);
  readonly viewTy = signal(20);
  readonly viewScale = signal(1);
  readonly svgTransform = computed(
    () => `translate(${this.viewTx()},${this.viewTy()}) scale(${this.viewScale()})`,
  );

  private svgElement = signal<SVGSVGElement | null>(null);
  readonly centerX = computed(() => {
    const svg = this.svgElement();
    if (!svg) return Number.NaN;
    return (svg.clientWidth / 2 - this.viewTx()) / this.viewScale();
  });
  readonly centerY = computed(() => {
    const svg = this.svgElement();
    if (!svg) return Number.NaN;
    return (svg.clientHeight / 2 - this.viewTy()) / this.viewScale();
  });

  registerSvgElement(svg: SVGSVGElement | null): void {
    this.svgElement.set(svg);
  }

  private dragging: { id: string; ox: number; oy: number } | null = null;
  private panning: { mx: number; my: number; tx: number; ty: number } | null = null;
  private didDrag = false;

  onBackgroundMouseDown(event: MouseEvent): void {
    if ((event.target as Element).closest('.ch-node')) return;
    this.panning = {
      mx: event.clientX,
      my: event.clientY,
      tx: this.viewTx(),
      ty: this.viewTy(),
    };
  }

  onNodeMouseDown(event: MouseEvent, node: CanvasNode, svg: SVGSVGElement): void {
    event.stopPropagation();
    const point = this.toCanvas(event, svg);
    this.dragging = { id: node.id, ox: point.x - node.x, oy: point.y - node.y };
    this.didDrag = false;
  }

  onMouseMove(event: MouseEvent, svg: SVGSVGElement, moveNode: (id: string, x: number, y: number) => void): void {
    if (this.dragging) {
      const point = this.toCanvas(event, svg);
      moveNode(this.dragging.id, Math.max(0, point.x - this.dragging.ox), Math.max(0, point.y - this.dragging.oy));
      this.didDrag = true;
    }
    if (this.panning) {
      this.viewTx.set(this.panning.tx + event.clientX - this.panning.mx);
      this.viewTy.set(this.panning.ty + event.clientY - this.panning.my);
    }
  }

  onMouseUp(): void {
    this.dragging = null;
    this.panning = null;
  }

  onWheel(event: WheelEvent, svg: SVGSVGElement): void {
    event.preventDefault();
    const oldScale = this.viewScale();
    const nextScale = Math.max(0.15, Math.min(3, oldScale * (event.deltaY > 0 ? 0.92 : 1.08)));
    const rect = svg.getBoundingClientRect();
    const mouseX = event.clientX - rect.left;
    const mouseY = event.clientY - rect.top;
    this.viewScale.set(nextScale);
    this.viewTx.set(mouseX - (mouseX - this.viewTx()) * (nextScale / oldScale));
    this.viewTy.set(mouseY - (mouseY - this.viewTy()) * (nextScale / oldScale));
  }

  consumeDrag(): boolean {
    const dragged = this.didDrag;
    this.didDrag = false;
    return dragged;
  }

  zoomIn(): void { this.viewScale.update(scale => Math.min(3, scale * 1.15)); }
  zoomOut(): void { this.viewScale.update(scale => Math.max(0.15, scale * 0.87)); }
  reset(): void { this.viewScale.set(1); this.viewTx.set(40); this.viewTy.set(20); }

  private toCanvas(event: MouseEvent, svg: SVGSVGElement): { x: number; y: number } {
    const rect = svg.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left - this.viewTx()) / this.viewScale(),
      y: (event.clientY - rect.top - this.viewTy()) / this.viewScale(),
    };
  }
}
