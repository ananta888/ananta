import { Inject, Injectable, InjectionToken, OnDestroy } from '@angular/core';

import type { SfuBroadcastJsonObject } from './sfu-broadcast-contracts';
import type { ValidatedLayerProjection } from './sfu-layer-projection.validator';
import type { SfuSubscriptionLayerPort } from './sfu-room-session.ports';
import type { SfuReceiverVideoQuality } from './livekit-sfu-receiver-layer.adapter';

export interface SfuReceiverLayerControllerEnvironment {
  nowMs(): number;
  setTimer(callback: () => void, delayMs: number): ReturnType<typeof setTimeout>;
  clearTimer(timer: ReturnType<typeof setTimeout>): void;
}

export const SFU_RECEIVER_LAYER_CONTROLLER_ENVIRONMENT =
  new InjectionToken<SfuReceiverLayerControllerEnvironment>('SFU_RECEIVER_LAYER_CONTROLLER_ENVIRONMENT', {
    providedIn: 'root',
    factory: () => ({
      nowMs: () => Date.now(),
      setTimer: (callback, delayMs) => globalThis.setTimeout(callback, delayMs),
      clearTimer: timer => globalThis.clearTimeout(timer),
    }),
  });

export interface SfuReceiverQualitySignal {
  readonly subscriptionRef: string;
  readonly qualityBasisPoints: number;
  readonly observedAtMs: number;
}

export interface SfuReceiverLayerOutcome {
  readonly outcome: 'applied' | 'unsupported' | 'fallback' | 'denied';
  readonly reasonCode: string;
  readonly quality: SfuReceiverVideoQuality;
}

export interface SfuReceiverLayerBinding {
  readonly subscriptionRef: string;
  readonly publicationRef: string;
  readonly port: SfuSubscriptionLayerPort;
  readonly projection: ValidatedLayerProjection;
  readonly onOutcome?: (outcome: SfuReceiverLayerOutcome) => void;
}

interface ActiveBinding {
  readonly generation: number;
  readonly binding: SfuReceiverLayerBinding;
  readonly minimum: number;
  readonly maximum: number;
  readonly expiresAtMs: number;
  current: number;
  pending: number;
  consecutive: number;
  lastAppliedAtMs: number;
  transitions: number[];
  timers: Set<ReturnType<typeof setTimeout>>;
  expired: boolean;
}

interface PreparedBinding {
  readonly active: ActiveBinding;
  readonly document: SfuBroadcastJsonObject;
  readonly effective: number;
}

const UPGRADE_THRESHOLD = 700;
const DOWNGRADE_THRESHOLD = 450;
const UPGRADE_CONSECUTIVE = 3;
const DOWNGRADE_CONSECUTIVE = 2;
const DWELL_MS = 3000;
const COOLDOWN_MS = 5000;
const RETRY_MAX = 2;
const TRANSITIONS_PER_MINUTE_MAX = 8;

@Injectable({ providedIn: 'root' })
export class SfuBroadcastReceiverLayerControllerService implements OnDestroy {
  private readonly active = new Map<string, ActiveBinding>();
  private generation = 0;

  constructor(
    @Inject(SFU_RECEIVER_LAYER_CONTROLLER_ENVIRONMENT) private readonly environment: SfuReceiverLayerControllerEnvironment,
  ) {}

  bind(binding: SfuReceiverLayerBinding): void {
    void this.activate(this.prepare(binding), true);
  }

  bindAndApply(binding: SfuReceiverLayerBinding): Promise<boolean> {
    return this.activate(this.prepare(binding), false);
  }

  private prepare(binding: SfuReceiverLayerBinding): PreparedBinding {
    if (binding.projection.kind !== 'receiver') throw new Error('sfu_receiver_projection_required');
    const document = binding.projection.contract.document as unknown as SfuBroadcastJsonObject;
    if (document['subscription_ref'] !== binding.subscriptionRef
        || document['publication_ref'] !== binding.publicationRef) {
      throw new Error('sfu_receiver_projection_scope_mismatch');
    }
    const corridor = object(document['corridor']);
    const minimum = spatial(corridor?.['minimum_layer'] ?? corridor?.['min_layer']);
    const maximum = spatial(corridor?.['maximum_layer'] ?? corridor?.['max_layer']);
    const effective = spatial(document['effective_layer']);
    const expiresAtMs = Date.parse(String(document['expires_at'] ?? ''));
    if (minimum === null || maximum === null || effective === null || minimum > maximum
        || effective < minimum || effective > maximum || !Number.isFinite(expiresAtMs)) {
      throw new Error('sfu_receiver_projection_corridor_invalid');
    }
    const active: ActiveBinding = {
      generation: ++this.generation, binding, minimum, maximum, expiresAtMs,
      current: minimum, pending: minimum, consecutive: 0, lastAppliedAtMs: this.environment.nowMs(),
      transitions: [], timers: new Set(), expired: expiresAtMs <= this.environment.nowMs(),
    };
    return Object.freeze({ active, document, effective });
  }

  private async activate(prepared: PreparedBinding, scheduleRetries: boolean): Promise<boolean> {
    const { active, document, effective } = prepared;
    const { binding } = active;
    this.stop(binding.subscriptionRef);
    this.active.set(binding.subscriptionRef, active);
    if (binding.port.layerControlMode !== 'manual_quality') {
      binding.onOutcome?.({
        outcome: 'unsupported', reasonCode: 'adaptive_stream_observation_only', quality: quality(active.minimum),
      });
      return true;
    }
    if (active.expired) {
      return this.apply(active, active.minimum, 'projection_stale', 0, scheduleRetries);
    }
    const expiryTimer = this.environment.setTimer(() => {
      active.timers.delete(expiryTimer);
      this.expire(active);
    }, Math.max(0, active.expiresAtMs - this.environment.nowMs()));
    active.timers.add(expiryTimer);
    if (document['resolution'] !== 'applied' || document['safe_outcome'] !== 'apply_projection') {
      return this.apply(active, active.minimum, 'lowest_safe_layer_applied', 0, scheduleRetries);
    }
    return this.apply(active, effective, 'applied_as_projected', 0, scheduleRetries);
  }

  observe(signal: SfuReceiverQualitySignal): void {
    const active = this.active.get(signal.subscriptionRef);
    if (!active || active.binding.port.layerControlMode !== 'manual_quality') return;
    const now = this.environment.nowMs();
    if (!Number.isSafeInteger(signal.observedAtMs) || signal.observedAtMs > now + 1000 || signal.observedAtMs < now - 5000) return;
    if (active.expired || now >= active.expiresAtMs) {
      this.expire(active);
      return;
    }
    const requested = signal.qualityBasisPoints >= UPGRADE_THRESHOLD
      ? active.maximum
      : signal.qualityBasisPoints <= DOWNGRADE_THRESHOLD ? active.minimum : active.current;
    const target = Math.min(active.maximum, Math.max(active.minimum, requested));
    if (target === active.current) {
      active.pending = target;
      active.consecutive = 0;
      return;
    }
    if (target !== active.pending) {
      active.pending = target;
      active.consecutive = 1;
      return;
    }
    active.consecutive += 1;
    const required = target > active.current ? UPGRADE_CONSECUTIVE : DOWNGRADE_CONSECUTIVE;
    if (active.consecutive < required || now - active.lastAppliedAtMs < DWELL_MS) return;
    active.transitions = active.transitions.filter(stamp => stamp > now - 60_000);
    if (active.transitions.length >= TRANSITIONS_PER_MINUTE_MAX) return;
    if (target > active.current && now - active.lastAppliedAtMs < COOLDOWN_MS) return;
    void this.apply(active, target, 'applied_as_projected');
  }

  fallback(subscriptionRef: string, reasonCode = 'projection_stale'): void {
    const active = this.active.get(subscriptionRef);
    if (active?.binding.port.layerControlMode === 'manual_quality') {
      void this.apply(active, active.minimum, reasonCode);
    }
  }

  /** Applies a conservative external cap without bypassing the signed projection corridor. */
  applyMinimum(subscriptionRef: string, reasonCode: string): Promise<boolean> {
    const active = this.active.get(subscriptionRef);
    if (!active || active.binding.port.layerControlMode !== 'manual_quality') {
      return Promise.resolve(false);
    }
    return this.apply(active, active.minimum, reasonCode, 0, false);
  }

  stop(subscriptionRef: string): void {
    const active = this.active.get(subscriptionRef);
    if (!active) return;
    this.active.delete(subscriptionRef);
    for (const timer of active.timers) this.environment.clearTimer(timer);
    active.timers.clear();
  }

  stopAll(): void {
    for (const key of [...this.active.keys()]) this.stop(key);
  }

  ngOnDestroy(): void { this.stopAll(); }

  private expire(active: ActiveBinding): void {
    if (this.active.get(active.binding.subscriptionRef)?.generation !== active.generation || active.expired) return;
    active.expired = true;
    for (const timer of active.timers) this.environment.clearTimer(timer);
    active.timers.clear();
    void this.apply(active, active.minimum, 'projection_stale');
  }

  private async apply(
    active: ActiveBinding,
    target: number,
    reasonCode: string,
    attempt = 0,
    scheduleRetries = true,
  ): Promise<boolean> {
    if (this.active.get(active.binding.subscriptionRef)?.generation !== active.generation) return false;
    const setter = active.binding.port.setRemotePublicationQuality;
    if (!setter) {
      active.binding.onOutcome?.({ outcome: 'unsupported', reasonCode: 'layer_unsupported', quality: quality(active.minimum) });
      return false;
    }
    if (this.environment.nowMs() >= active.expiresAtMs) active.expired = true;
    const bounded = active.expired
      ? active.minimum
      : Math.min(active.maximum, Math.max(active.minimum, target));
    const effectiveReason = active.expired ? 'projection_stale' : reasonCode;
    try {
      await setter.call(active.binding.port, active.binding.publicationRef, quality(bounded));
      if (this.active.get(active.binding.subscriptionRef)?.generation !== active.generation) return false;
      if ((active.expired || this.environment.nowMs() >= active.expiresAtMs) && bounded !== active.minimum) {
        active.expired = true;
        return this.apply(active, active.minimum, 'projection_stale', 0, scheduleRetries);
      }
      const now = this.environment.nowMs();
      active.current = bounded;
      active.pending = bounded;
      active.consecutive = 0;
      active.lastAppliedAtMs = now;
      active.transitions.push(now);
      const appliedReason = active.expired ? 'projection_stale' : effectiveReason;
      active.binding.onOutcome?.({
        outcome: appliedReason === 'projection_stale' ? 'fallback' : 'applied',
        reasonCode: appliedReason, quality: quality(bounded),
      });
      return true;
    } catch {
      if (!scheduleRetries || attempt >= RETRY_MAX) {
        active.binding.onOutcome?.({ outcome: 'fallback', reasonCode: 'ordinary_fallback_selected', quality: quality(active.minimum) });
        return false;
      }
      const timer = this.environment.setTimer(() => {
        active.timers.delete(timer);
        void this.apply(
          active,
          active.expired ? active.minimum : bounded,
          active.expired ? 'projection_stale' : effectiveReason,
          attempt + 1,
          scheduleRetries,
        );
      }, 250 * (2 ** attempt));
      active.timers.add(timer);
      return false;
    }
  }
}

function object(value: unknown): SfuBroadcastJsonObject | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as SfuBroadcastJsonObject : null;
}

function spatial(value: unknown): number | null {
  const layer = object(value);
  const result = layer?.['spatial_id'];
  return Number.isSafeInteger(result) && Number(result) >= 0 && Number(result) <= 3 ? Number(result) : null;
}

function quality(spatialId: number): SfuReceiverVideoQuality {
  return spatialId <= 0 ? 'low' : spatialId === 1 ? 'medium' : 'high';
}
