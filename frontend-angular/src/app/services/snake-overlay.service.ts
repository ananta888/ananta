import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

const LS_KEY = 'ananta.snake-overlay.v1';

@Injectable({ providedIn: 'root' })
export class SnakeOverlayService {
  readonly visible$ = new BehaviorSubject<boolean>(this.loadPref());

  toggle(): void {
    const next = !this.visible$.value;
    this.visible$.next(next);
    try { localStorage.setItem(LS_KEY, String(next)); } catch {}
  }

  private loadPref(): boolean {
    try { return localStorage.getItem(LS_KEY) === 'true'; } catch { return false; }
  }
}
