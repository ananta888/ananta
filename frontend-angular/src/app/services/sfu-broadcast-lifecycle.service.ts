import { Injectable, OnDestroy, inject } from '@angular/core';
import { Subscription } from 'rxjs';

import { LivekitSfuTransportService } from './livekit-sfu-transport.service';
import {
  SfuBroadcastQualityReporterService,
  type SfuQualityReporterBinding,
} from './sfu-broadcast-quality-reporter.service';
import type { SfuRemoteVideoHandle } from './sfu-room-session.ports';

/** Composes lifecycle only; transport remains unaware of API and retry policy. */
@Injectable({ providedIn: 'root' })
export class SfuBroadcastLifecycleService implements OnDestroy {
  private readonly transport = inject(LivekitSfuTransportService);
  private readonly reporter = inject(SfuBroadcastQualityReporterService);
  private readonly subscriptions = new Subscription();
  private binding: SfuQualityReporterBinding | null = null;
  private activeHandle: SfuRemoteVideoHandle | null = null;

  constructor() {
    this.subscriptions.add(this.transport.remoteVideo$.subscribe(handle => this.onVideo(handle)));
    this.subscriptions.add(this.transport.remoteVideoUnavailable$.subscribe(value => {
      if (value.handleId === this.activeHandle?.handleId) this.stopReporting();
    }));
    this.subscriptions.add(this.transport.state$.subscribe(state => {
      if (state.status !== 'connected') this.stopReporting();
    }));
  }

  bindQuality(binding: SfuQualityReporterBinding | null): void {
    this.reporter.stop();
    this.activeHandle = null;
    this.binding = binding;
  }

  revoke(): void {
    this.binding = null;
    this.stopReporting();
  }

  ngOnDestroy(): void {
    this.revoke();
    this.subscriptions.unsubscribe();
  }

  private onVideo(handle: SfuRemoteVideoHandle): void {
    const binding = this.binding;
    if (!binding || !this.transport.remoteVideoMatchesPublication(handle, binding.publicationRef)) return;
    const port = this.transport.statsPortFor(handle);
    if (!port) return;
    this.activeHandle = handle;
    this.reporter.start(binding, port, handle);
  }

  private stopReporting(): void {
    this.reporter.stop();
    this.activeHandle = null;
  }
}
