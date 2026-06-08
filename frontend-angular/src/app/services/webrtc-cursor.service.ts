/**
 * Broadcasts own mouse cursor position to pair-dev peers via WebrtcTransportService
 * and feeds incoming positions into SnakeOverlayService.remoteCursors$.
 *
 * Activate by injecting in SnakeOverlayComponent — no manual setup needed.
 * Wire in cursor positions from future transports by calling overlay.setRemoteCursor().
 */
import { Injectable, inject, OnDestroy } from '@angular/core';
import { combineLatest, fromEvent, Subscription } from 'rxjs';
import { throttleTime } from 'rxjs/operators';
import { WebrtcTransportService } from './webrtc-transport.service';
import { ShareSessionService } from './share-session.service';
import { SnakeOverlayService } from './snake-overlay.service';

@Injectable({ providedIn: 'root' })
export class WebrtcCursorService implements OnDestroy {
  private transport = inject(WebrtcTransportService);
  private share     = inject(ShareSessionService);
  private overlay   = inject(SnakeOverlayService);

  private mouseX = 0.5;
  private mouseY = 0.5;
  private sendHandle?: ReturnType<typeof setInterval>;
  private subs: Subscription[] = [];

  constructor() {
    // Track own mouse, normalised 0–1 for screen-size independence
    this.subs.push(
      fromEvent<MouseEvent>(document, 'mousemove')
        .pipe(throttleTime(40))
        .subscribe(e => {
          this.mouseX = e.clientX / window.innerWidth;
          this.mouseY = e.clientY / window.innerHeight;
        }),
    );

    // Receive cursor positions from peers
    this.subs.push(
      this.transport.message$.subscribe(msg => {
        if (msg.type !== 'cursor') return;
        const p = msg.payload as { x?: number; y?: number; sender_id?: string } | null;
        if (!p?.sender_id || p.sender_id === this.myId) return;
        this.overlay.setRemoteCursor(
          p.sender_id,
          (p.x ?? 0.5) * window.innerWidth,
          (p.y ?? 0.5) * window.innerHeight,
        );
      }),
    );

    // Broadcast while overlay is visible AND transport is active
    this.subs.push(
      combineLatest([this.overlay.visible$, this.transport.mode$])
        .subscribe(([visible, mode]) => {
          if (visible && mode !== 'idle') this.startBroadcast();
          else this.stopBroadcast();
        }),
    );

    // Clear remote cursors when pair-dev session ends
    this.subs.push(
      this.share.state$.subscribe(state => {
        if (!state.session) this.overlay.remoteCursors$.next(new Map());
      }),
    );
  }

  ngOnDestroy(): void {
    this.stopBroadcast();
    this.subs.forEach(s => s.unsubscribe());
  }

  private get myId(): string { return this.share.currentUserId; }

  private startBroadcast(): void {
    if (this.sendHandle) return;
    this.sendHandle = setInterval(() => this.broadcast(), 50);
  }

  private stopBroadcast(): void {
    if (this.sendHandle) { clearInterval(this.sendHandle); this.sendHandle = undefined; }
  }

  private broadcast(): void {
    if (this.transport.mode$.value === 'idle') return;
    this.transport.send('cursor', {
      sender_id: this.myId,
      x: this.mouseX,
      y: this.mouseY,
    });
  }
}
