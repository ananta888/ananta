/**
 * T10: Remote-Cursor Overlay
 *
 * Reads `peerCursors$` from PairViewSyncService and renders one
 * labelled <div> per active peer cursor. The overlay:
 *  - shows the peer's userLabel as a small label
 *  - lets the user turn the overlay on/off
 *  - reaps cursors that haven't been refreshed for the service's
 *    own timeout window (5s default)
 *  - is fixed/pointer-events: none — no layout shift, no
 *    interaction-blocking
 */
import { ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit, inject, signal } from '@angular/core';

import { Subscription } from 'rxjs';

import { PairViewSyncService, PeerCursor } from '../services/pair-view-sync.service';

@Component({
  selector: 'app-remote-cursor-overlay',
  standalone: true,
  imports: [],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
  @if (sync.cursorOverlayEnabled) {
    <div class="cursor-overlay" aria-hidden="true">
      @for (cursor of cursors(); track cursor.userId) {
        <div class="remote-cursor"
             [style.transform]="transformFor(cursor)"
             [attr.data-user-id]="cursor.userId"
             [attr.data-user-label]="cursor.userLabel">
          <svg class="cursor-arrow" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
            <path d="M1 1 L1 12 L4 9 L6 14 L8 13 L6 8 L11 8 Z"
                  fill="currentColor"
                  stroke="rgba(0,0,0,0.4)"
                  stroke-width="0.5"/>
          </svg>
          <span class="cursor-label">{{ cursor.userLabel }}</span>
        </div>
      }
    </div>
  }
  `,
  styles: [`
    :host {
      position: fixed; inset: 0; pointer-events: none; z-index: 29000;
    }
    .cursor-overlay {
      position: absolute; inset: 0; pointer-events: none;
    }
    .remote-cursor {
      position: absolute; top: 0; left: 0; pointer-events: none;
      will-change: transform;
      color: #ff6b6b; /* default peer color; could be parameterized */
    }
    .cursor-arrow { display: block; }
    .cursor-label {
      position: absolute; left: 14px; top: 14px;
      font: 11px/1.2 system-ui, sans-serif;
      padding: 2px 6px; border-radius: 3px;
      background: rgba(20,20,24,0.85); color: #f3f3f5;
      white-space: nowrap;
    }
  `],
})
export class RemoteCursorOverlayComponent implements OnInit, OnDestroy {
  sync = inject(PairViewSyncService);
  private cdr = inject(ChangeDetectorRef);
  readonly cursors = signal<ReadonlyArray<PeerCursor>>([]);
  private sub: Subscription | null = null;

  ngOnInit(): void {
    this.sub = this.sync.peerCursors$.subscribe((map) => {
      this.cursors.set(Array.from(map.values()));
      this.cdr.markForCheck();
    });
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }

  /**
   * Returns a CSS transform string for the given peer cursor.
   * Pointer-cursor (x/y) takes priority: it is the natural unit
   * for the overlay. Falls back to a 12-px-per-line, 7-px-per-col
   * approximation of the text-cursor position so the overlay
   * still works for non-pointer senders.
   */
  transformFor(cursor: PeerCursor): string {
    let x: number;
    let y: number;
    if (typeof cursor.cursor.x === 'number' && typeof cursor.cursor.y === 'number') {
      x = cursor.cursor.x;
      y = cursor.cursor.y;
    } else {
      x = ((cursor.cursor.column ?? 0) * 7) + 4;
      y = ((cursor.cursor.line ?? 0) * 12) + 4;
    }
    x = Math.max(0, Math.round(x));
    y = Math.max(0, Math.round(y));
    return `translate3d(${x}px, ${y}px, 0)`;
  }
}
