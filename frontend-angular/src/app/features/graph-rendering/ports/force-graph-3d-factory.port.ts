import { inject, Injectable, InjectionToken } from '@angular/core';

import type { ForceGraph3DInstance } from '3d-force-graph';

export interface ForceGraph3dFactoryPort {
  webglAvailable(): boolean;
  create(host: HTMLElement): Promise<ForceGraph3DInstance>;
}

@Injectable({ providedIn: 'root' })
export class BrowserForceGraph3dFactory implements ForceGraph3dFactoryPort {
  webglAvailable(): boolean {
    try {
      const canvas = document.createElement('canvas');
      return Boolean(canvas.getContext('webgl') || canvas.getContext('experimental-webgl'));
    } catch {
      return false;
    }
  }

  async create(host: HTMLElement): Promise<ForceGraph3DInstance> {
    const { default: ForceGraph3D } = await import('3d-force-graph');
    return new ForceGraph3D(host, { controlType: 'orbit' });
  }
}

export const FORCE_GRAPH_3D_FACTORY = new InjectionToken<ForceGraph3dFactoryPort>(
  'FORCE_GRAPH_3D_FACTORY',
  {
    providedIn: 'root',
    factory: () => inject(BrowserForceGraph3dFactory),
  },
);
