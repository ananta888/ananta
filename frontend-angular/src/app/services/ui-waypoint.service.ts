import { Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class UiWaypointService {
  resolve(name: string): { x: number; y: number } | null {
    try {
      const el = document.querySelector(`[data-waypoint="${CSS.escape(name)}"]`);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return null;
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    } catch {
      return null;
    }
  }
}
