import { InjectionToken } from '@angular/core';

export interface CanvasPoint {
  x: number;
  y: number;
}

export interface CanvasViewport {
  pan: CanvasPoint;
  zoom: number;
  width: number;
  height: number;
}

export interface CanvasViewportPort {
  clampZoom(value: number): number;
  zoomAt(viewport: CanvasViewport, pointer: CanvasPoint, factor: number): CanvasViewport;
  fit(points: readonly CanvasPoint[], width: number, height: number, padding?: number): CanvasViewport;
}

export const CANVAS_VIEWPORT_PORT = new InjectionToken<CanvasViewportPort>('CANVAS_VIEWPORT_PORT');

export class DefaultCanvasViewportAdapter implements CanvasViewportPort {
  clampZoom(value: number): number {
    return Math.min(2.5, Math.max(.25, value));
  }

  zoomAt(viewport: CanvasViewport, pointer: CanvasPoint, factor: number): CanvasViewport {
    const nextZoom = this.clampZoom(viewport.zoom * factor);
    const ratio = nextZoom / viewport.zoom;
    return {
      ...viewport,
      zoom: nextZoom,
      pan: {
        x: pointer.x - (pointer.x - viewport.pan.x) * ratio,
        y: pointer.y - (pointer.y - viewport.pan.y) * ratio,
      },
    };
  }

  fit(points: readonly CanvasPoint[], width: number, height: number, padding = 70): CanvasViewport {
    if (!points.length) return { pan: { x: width / 2, y: height / 2 }, zoom: 1, width, height };
    const xs = points.map(point => point.x);
    const ys = points.map(point => point.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const graphWidth = Math.max(1, maxX - minX + 180);
    const graphHeight = Math.max(1, maxY - minY + 90);
    const zoom = this.clampZoom(Math.min((width - padding * 2) / graphWidth, (height - padding * 2) / graphHeight));
    return {
      pan: {
        x: width / 2 - ((minX + maxX) / 2) * zoom,
        y: height / 2 - ((minY + maxY) / 2) * zoom,
      },
      zoom,
      width,
      height,
    };
  }
}
