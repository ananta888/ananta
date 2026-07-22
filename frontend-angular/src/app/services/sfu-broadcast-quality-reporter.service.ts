import { Injectable, InjectionToken, OnDestroy, inject } from '@angular/core';
import { Subscription } from 'rxjs';

import type {
  ReceiverQualityObservationContract,
  SfuBroadcastJsonValue,
} from './sfu-broadcast-contracts';
import {
  SfuBroadcastQualityApiService,
  type SfuQualityApiScope,
} from './sfu-broadcast-quality-api.service';
import {
  SfuBroadcastQualitySamplerService,
  type SfuBroadcastQualitySample,
} from './sfu-broadcast-quality-sampler.service';
import type { SfuRemoteVideoHandle, SfuStatsPort } from './sfu-room-session.ports';

export interface SfuQualityReporterBinding extends SfuQualityApiScope {
  readonly tenantRef: string;
  readonly roomRef: string;
  readonly subscriberRef: string;
  readonly publicationRef: string;
  readonly routeEpoch: number;
  readonly requestedLayer: SfuBroadcastJsonValue;
  readonly allowedLayer: SfuBroadcastJsonValue;
  readonly effectiveLayer: SfuBroadcastJsonValue;
}

export interface SfuQualityReporterEnvironment {
  nowMs(): number;
  randomBytes(length: number): Uint8Array;
  online(): boolean;
  hidden(): boolean;
  setTimer(callback: () => void, delayMs: number): ReturnType<typeof setTimeout>;
  clearTimer(timer: ReturnType<typeof setTimeout>): void;
}

export const SFU_QUALITY_REPORTER_ENVIRONMENT = new InjectionToken<SfuQualityReporterEnvironment>(
  'SFU_QUALITY_REPORTER_ENVIRONMENT',
  { providedIn: 'root', factory: nativeEnvironment },
);

const REPORT_INTERVAL_MS = 5_000;
const SAMPLE_INTERVAL_MS = 1_000;
const SAMPLES_MAX = 16;
const REPORT_BYTES_MAX = 8_192;

@Injectable({ providedIn: 'root' })
export class SfuBroadcastQualityReporterService implements OnDestroy {
  private readonly api = inject(SfuBroadcastQualityApiService);
  private readonly sampler = inject(SfuBroadcastQualitySamplerService);
  private readonly environment = inject(SFU_QUALITY_REPORTER_ENVIRONMENT);
  private timer: ReturnType<typeof setTimeout> | null = null;
  private request: Subscription | null = null;
  private binding: SfuQualityReporterBinding | null = null;
  private port: SfuStatsPort | null = null;
  private handle: SfuRemoteVideoHandle | null = null;
  private samples: SfuBroadcastQualitySample[] = [];
  private pending: ReceiverQualityObservationContract | null = null;
  private pendingAtMs = 0;
  private sequence = 0;
  private generation = 0;
  private failures = 0;
  private pseudonym = '';
  private lastReportAtMs = 0;

  start(binding: SfuQualityReporterBinding, port: SfuStatsPort, handle: SfuRemoteVideoHandle): void {
    validateBinding(binding, port, handle);
    this.stop();
    this.binding = binding;
    this.port = port;
    this.handle = handle;
    this.pseudonym = `room-bip_${base64Url(this.environment.randomBytes(16))}`;
    this.lastReportAtMs = this.environment.nowMs();
    const generation = ++this.generation;
    this.schedule(generation, SAMPLE_INTERVAL_MS);
  }

  stop(): void {
    this.generation += 1;
    if (this.timer !== null) this.environment.clearTimer(this.timer);
    this.timer = null;
    this.request?.unsubscribe();
    this.request = null;
    if (this.handle) this.sampler.reset(this.handle.handleId);
    this.binding = null;
    this.port = null;
    this.handle = null;
    this.samples = [];
    this.pending = null;
    this.failures = 0;
    this.pseudonym = '';
  }

  ngOnDestroy(): void { this.stop(); }

  private schedule(generation: number, delayMs: number): void {
    if (generation !== this.generation || !this.binding) return;
    if (this.timer !== null) this.environment.clearTimer(this.timer);
    this.timer = this.environment.setTimer(() => {
      this.timer = null;
      void this.tick(generation);
    }, delayMs);
  }

  private async tick(generation: number): Promise<void> {
    const binding = this.binding;
    const port = this.port;
    const handle = this.handle;
    if (generation !== this.generation || !binding || !port || !handle) return;
    let nextDelay = SAMPLE_INTERVAL_MS;
    try {
      if (!this.environment.online() || this.environment.hidden()) {
        nextDelay = REPORT_INTERVAL_MS;
        return;
      }
      if (!this.pending) {
        const sample = await this.sampler.sample(port, handle);
        if (generation !== this.generation) return;
        if (sample) this.samples.push(sample);
        if (this.samples.length > SAMPLES_MAX) this.samples.splice(0, this.samples.length - SAMPLES_MAX);
        if (this.samples.length && this.environment.nowMs() - this.lastReportAtMs >= REPORT_INTERVAL_MS) {
          this.pending = await this.buildReport(binding);
          this.pendingAtMs = this.environment.nowMs();
        }
      }
      if (!this.pending) return;
      if (this.environment.nowMs() - this.pendingAtMs >= 4_500) {
        this.pending = null;
        this.failures = 0;
        return;
      }
      const sent = await this.transmit(binding, this.pending, generation);
      if (generation !== this.generation) return;
      if (sent) {
        this.samples = [];
        this.pending = null;
        this.failures = 0;
        this.lastReportAtMs = this.environment.nowMs();
      } else {
        this.failures = Math.min(this.failures + 1, 4);
        nextDelay = Math.min(2_000, 250 * (2 ** (this.failures - 1)));
      }
    } finally {
      if (generation === this.generation && this.binding) this.schedule(generation, nextDelay);
    }
  }

  private async buildReport(binding: SfuQualityReporterBinding): Promise<ReceiverQualityObservationContract> {
    this.sequence += 1;
    let samples = [...this.samples];
    while (samples.length) {
      const unsigned = unsignedReport(binding, this.pseudonym, this.sequence, samples, this.environment.nowMs());
      const report = unsigned as ReceiverQualityObservationContract;
      if (encodedBytes(report) <= REPORT_BYTES_MAX) return report;
      samples = samples.slice(1);
    }
    throw new Error('sfu_quality_report_budget_exceeded');
  }

  private transmit(
    binding: SfuQualityReporterBinding,
    report: ReceiverQualityObservationContract,
    generation: number,
  ): Promise<boolean> {
    return new Promise(resolve => {
      let accepted = false;
      let settled = false;
      const finish = (value: boolean) => {
        if (settled) return;
        settled = true;
        resolve(value && generation === this.generation);
      };
      const subscription = this.api.submit(binding, report).subscribe({
        next: () => { accepted = true; },
        error: () => finish(false),
        complete: () => finish(accepted),
      });
      this.request = subscription;
      subscription.add(() => finish(accepted));
      if (subscription.closed && this.request === subscription) this.request = null;
    });
  }
}

function unsignedReport(
  binding: SfuQualityReporterBinding,
  pseudonym: string,
  sequence: number,
  samples: readonly SfuBroadcastQualitySample[],
  nowMs: number,
) {
  return Object.freeze({
    schema: 'ananta.receiver-quality-observation.v1' as const,
    schema_version: 1 as const,
    observation_version: 'bounded-v1' as const,
    tenant_ref: binding.tenantRef,
    room_ref: binding.roomRef,
    subscriber_ref: binding.subscriberRef,
    publication_ref: binding.publicationRef,
    browser_instance_pseudonym: pseudonym,
    route_epoch: binding.routeEpoch,
    sequence,
    issued_at: utcSecond(nowMs),
    authorization_effect: 'none' as const,
    advisory_only: true as const,
    limits: Object.freeze({
      history_reports_max: 12,
      samples_per_report_max: 16,
      reports_per_minute_max: 12,
      report_bytes_max: 8192,
      sample_window_ms_max: 2000,
      history_window_ms_max: 30000,
      observation_age_ms_max: 5000,
    }),
    requested_layer: binding.requestedLayer,
    allowed_layer: binding.allowedLayer,
    effective_layer: binding.effectiveLayer,
    samples: Object.freeze([...samples]),
  });
}

function validateBinding(
  binding: SfuQualityReporterBinding,
  port: SfuStatsPort,
  handle: SfuRemoteVideoHandle,
): void {
  for (const value of [
    binding.tenantRef, binding.roomRef, binding.subscriberRef, binding.subscriptionRef,
    binding.publicationRef, binding.sessionId,
  ]) {
    if (!value || new TextEncoder().encode(value).byteLength > 128 || /[\u0000-\u0020\u007f]/.test(value)) {
      throw new Error('sfu_quality_binding_invalid');
    }
  }
  if (!Number.isSafeInteger(binding.routeEpoch)
      || binding.routeEpoch < 1 || !Number.isSafeInteger(binding.membershipEpoch)
      || binding.membershipEpoch < 1
      || port.authorizesQualityBinding?.(handle, binding) !== true) {
    throw new Error('sfu_quality_binding_invalid');
  }
}

function nativeEnvironment(): SfuQualityReporterEnvironment {
  return Object.freeze({
    nowMs: () => Date.now(),
    randomBytes: (length: number) => globalThis.crypto.getRandomValues(new Uint8Array(length)),
    online: () => typeof navigator === 'undefined' || navigator.onLine !== false,
    hidden: () => typeof document !== 'undefined' && document.visibilityState === 'hidden',
    setTimer: (callback: () => void, delayMs: number) => globalThis.setTimeout(callback, delayMs),
    clearTimer: (timer: ReturnType<typeof setTimeout>) => globalThis.clearTimeout(timer),
  });
}

function utcSecond(nowMs: number): string {
  return new Date(Math.floor(nowMs / 1_000) * 1_000).toISOString().replace('.000Z', 'Z');
}

function encodedBytes(value: unknown): number { return new TextEncoder().encode(JSON.stringify(value)).byteLength; }

function base64Url(bytes: Uint8Array): string {
  if (bytes.length !== 16) throw new Error('sfu_quality_randomness_invalid');
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  let bits = 0;
  let value = 0;
  let output = '';
  for (const byte of bytes) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 6) {
      bits -= 6;
      output += alphabet[(value >>> bits) & 63];
    }
  }
  if (bits > 0) output += alphabet[(value << (6 - bits)) & 63];
  return output;
}
