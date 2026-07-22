import { Injectable, inject } from '@angular/core';

import {
  SfuBroadcastVideoRenderService,
  type SfuRemoteVideoView,
} from './sfu-broadcast-video-render.service';
import type { SfuRelease } from './sfu-room-session.ports';

export type { SfuRemoteVideoView } from './sfu-broadcast-video-render.service';

@Injectable({ providedIn: 'root' })
export class SfuBroadcastVideoRenderFacade {
  private readonly service = inject(SfuBroadcastVideoRenderService);
  readonly videos$ = this.service.videos$;

  attach(view: SfuRemoteVideoView, target: HTMLVideoElement): SfuRelease {
    return this.service.attach(view, target);
  }

  clear(): void {
    this.service.clear();
  }
}
