import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

const LS_KEY = 'ananta.snake-overlay.v1';

@Injectable({ providedIn: 'root' })
export class SnakeOverlayService {
  readonly visible$ = new BehaviorSubject<boolean>(this.loadPref());

  /** Remote cursor positions keyed by participant ID.
   *  Call from WebRTC data-channel handler when cursor positions arrive. */
  readonly remoteCursors$ = new BehaviorSubject<Map<string, { x: number; y: number }>>(new Map());

  setRemoteCursor(id: string, x: number, y: number): void {
    const map = new Map(this.remoteCursors$.value);
    map.set(id, { x, y });
    this.remoteCursors$.next(map);
  }

  clearRemoteCursor(id: string): void {
    const map = new Map(this.remoteCursors$.value);
    map.delete(id);
    this.remoteCursors$.next(map);
  }

  toggle(): void {
    const next = !this.visible$.value;
    this.visible$.next(next);
    try { localStorage.setItem(LS_KEY, String(next)); } catch {}
  }

  /** Region-select mode: user draws a rect → guide snake explains elements inside. */
  readonly regionMode$ = new BehaviorSubject<boolean>(false);

  toggleRegionMode(): void { this.regionMode$.next(!this.regionMode$.value); }
  exitRegionMode(): void   { this.regionMode$.next(false); }

  private loadPref(): boolean {
    try {
      const stored = localStorage.getItem(LS_KEY);
      return stored === null ? true : stored === 'true';
    } catch { return true; }
  }
}
