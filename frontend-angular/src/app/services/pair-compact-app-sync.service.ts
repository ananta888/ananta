import { DOCUMENT } from '@angular/common';
import { Injectable, OnDestroy, inject } from '@angular/core';
import { EMPTY, Subscription, fromEvent } from 'rxjs';
import { startWith, switchMap, throttleTime } from 'rxjs/operators';

import { PairViewSyncService } from './pair-view-sync.service';
import { PairViewSessionBindingService } from './pair-view-session-binding.service';
import { ShareSessionService } from './share-session.service';
import { SharedViewStateService } from './shared-view-state.service';

/**
 * Activates the compact, pixel-free Public Pair capture path.
 *
 * This coordinator knows only browser pointer/route capture and the encrypted
 * Pair-View port. It never starts media capture, reads the DOM, or talks to the
 * Hub/AiSnake services (SRP/DIP).
 */
@Injectable({ providedIn: 'root' })
export class PairCompactAppSyncService implements OnDestroy {
  private readonly document = inject(DOCUMENT);
  private readonly view = inject(SharedViewStateService);
  private readonly sync = inject(PairViewSyncService);
  private readonly binding = inject(PairViewSessionBindingService);
  private readonly share = inject(ShareSessionService);
  private readonly subscriptions = new Subscription();
  private started = false;

  start(): void {
    if (this.started) return;
    this.started = true;
    this.view.init();
    this.binding.start();
    const viewport = this.document.defaultView;
    if (!viewport) return;
    this.subscriptions.add(
      this.sync.localCompactSharing$.pipe(
        switchMap(consent => consent.cursor
          ? fromEvent<MouseEvent>(this.document, 'mousemove').pipe(
              throttleTime(50, undefined, { leading: true, trailing: true }),
            )
          : EMPTY),
      ).subscribe(event => {
        if (this.share.publicPairRuntimeState$.value !== 'public') return;
        const width = Math.max(1, viewport.innerWidth);
        const height = Math.max(1, viewport.innerHeight);
        this.sync.sendCursor(compactPeerLabel(this.share.currentUserId), {
          line: null,
          column: null,
          nx: clamp(event.clientX / width),
          ny: clamp(event.clientY / height),
        });
      }),
    );
    this.subscriptions.add(
      this.sync.localCompactSharing$.pipe(
        switchMap(consent => consent.view
          ? fromEvent(viewport, 'scroll').pipe(
              startWith(null),
              throttleTime(100, undefined, { leading: true, trailing: true }),
            )
          : EMPTY),
      ).subscribe(() => {
        if (this.share.publicPairRuntimeState$.value !== 'public') return;
        this.view.updateScroll({
          x: boundedViewportCoordinate(viewport.scrollX),
          y: boundedViewportCoordinate(viewport.scrollY),
        });
      }),
    );
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }
}

function clamp(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function boundedViewportCoordinate(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(-10_000_000, Math.min(10_000_000, value));
}

/** Non-secret, stable label; never sends a username or the full peer id. */
export function compactPeerLabel(peerId: string): string {
  let hash = 2166136261;
  for (let index = 0; index < peerId.length; index += 1) {
    hash ^= peerId.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `Peer-${(hash >>> 0).toString(16).padStart(8, '0').slice(0, 6)}`;
}
