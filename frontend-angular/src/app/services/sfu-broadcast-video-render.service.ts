import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Subscription } from 'rxjs';

import { LivekitSfuTransportService } from './livekit-sfu-transport.service';
import type { SfuRelease, SfuRemoteVideoHandle } from './sfu-room-session.ports';

export interface SfuRemoteVideoView {
  readonly handle: SfuRemoteVideoHandle;
  readonly accessibleName: string;
  readonly state: 'active' | 'muted';
}

interface RenderRow {
  readonly view: SfuRemoteVideoView;
  readonly releases: Set<SfuRelease>;
}

const REMOTE_VIDEO_ROWS_MAX = 32;

/** UI-safe projection over the current transport session's opaque render port. */
@Injectable({ providedIn: 'root' })
export class SfuBroadcastVideoRenderService implements OnDestroy {
  private readonly transport = inject(LivekitSfuTransportService);
  private readonly subscriptions = new Subscription();
  private readonly rows = new Map<string, RenderRow>();
  readonly videos$ = new BehaviorSubject<readonly SfuRemoteVideoView[]>(Object.freeze([]));

  constructor() {
    this.subscriptions.add(this.transport.remoteVideo$.subscribe(handle => {
      if (!this.rows.has(handle.handleId) && this.rows.size >= REMOTE_VIDEO_ROWS_MAX) return;
      this.remove(handle.handleId);
      this.rows.set(handle.handleId, {
        view: Object.freeze({
          handle,
          accessibleName: handle.source === 'screen'
            ? 'Autorisierte Remote-Bildschirmfreigabe'
            : 'Autorisierte Remote-Kamera',
          state: 'active',
        }),
        releases: new Set(),
      });
      this.emit();
    }));
    this.subscriptions.add(this.transport.remoteVideoUnavailable$.subscribe(value => {
      this.remove(value.handleId);
      this.emit();
    }));
    this.subscriptions.add(this.transport.remoteVideoState$.subscribe(value => {
      const row = this.rows.get(value.handleId);
      if (!row) return;
      this.rows.set(value.handleId, {
        ...row,
        view: Object.freeze({ ...row.view, state: value.state }),
      });
      this.emit();
    }));
    this.subscriptions.add(this.transport.state$.subscribe(state => {
      if (state.status !== 'connected') this.clear();
    }));
  }

  attach(view: SfuRemoteVideoView, target: HTMLVideoElement): SfuRelease {
    const row = this.rows.get(view.handle.handleId);
    if (!row || row.view !== view) throw new Error('sfu_video_handle_stale');
    const transportRelease = this.transport.attachRemoteVideo(view.handle, target);
    let active = true;
    const release = () => {
      if (!active) return;
      active = false;
      row.releases.delete(release);
      transportRelease();
    };
    row.releases.add(release);
    return release;
  }

  clear(): void {
    for (const handleId of [...this.rows.keys()]) this.remove(handleId);
    this.emit();
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
    this.clear();
    this.videos$.complete();
  }

  private remove(handleId: string): void {
    const row = this.rows.get(handleId);
    if (!row) return;
    for (const release of [...row.releases].reverse()) release();
    row.releases.clear();
    this.rows.delete(handleId);
  }

  private emit(): void {
    this.videos$.next(Object.freeze([...this.rows.values()]
      .map(row => row.view)
      .sort((left, right) => left.handle.handleId.localeCompare(right.handle.handleId))));
  }
}
