import { Inject, Injectable, InjectionToken, OnDestroy } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

import { SfuBroadcastReceiverLayerControllerService } from './sfu-broadcast-receiver-layer-controller.service';
import type { SfuSubscriptionLayerPort } from './sfu-room-session.ports';

export type TurnDegradationState =
  | 'direct' | 'relay_ok' | 'relay_capped' | 'control_only' | 'fallback' | 'rejected';
export type TurnDegradationAllowedLayer = 'high' | 'low' | 'none';
export type TurnDegradationAllowedClass = 'control' | 'key' | 'transcript' | 'media';
export type TurnTransportStatus = 'healthy' | 'degraded' | 'unavailable';

export interface TurnDegradationHubStateDocument {
  readonly receiver_diagnostic_ref: string;
  readonly version: number;
  readonly state: TurnDegradationState;
  readonly reason_code: string;
  readonly allowed_layer: TurnDegradationAllowedLayer;
  readonly allowed_classes: readonly TurnDegradationAllowedClass[];
  readonly retry_count: number;
  readonly cooldown_until_seconds: number;
  readonly signature: string;
}

/** Produced only after the Hub-state signature boundary accepted the document. */
export interface HubValidatedTurnDegradationObservation {
  readonly validation: 'hub-turn-degradation-state-accepted-v1';
  readonly document: TurnDegradationHubStateDocument;
}

export interface TurnTransportObservation {
  readonly source: 'transport';
  readonly receiverDiagnosticRef: string;
  readonly status: TurnTransportStatus;
  readonly reasonCode:
    | 'turn_transport_healthy'
    | 'turn_transport_degraded'
    | 'turn_transport_unavailable'
    | 'turn_transport_network_changed';
  readonly observedAtMs: number;
}

export interface TurnDegradationSpeechPort {
  applyTurnAllowedClasses(classes: ReadonlySet<TurnDegradationAllowedClass>): void;
}

export interface TurnDegradationReceiverBinding {
  readonly receiverDiagnosticRef: string;
  readonly subscriptionRef: string;
  readonly publicationRef: string;
  readonly subscriptions: SfuSubscriptionLayerPort;
  readonly speech?: TurnDegradationSpeechPort;
  readonly onParentFallback?: (reasonCode: string) => void;
  readonly onRejected?: (reasonCode: string) => void;
}

export interface TurnDegradationReceiverView {
  readonly receiverDiagnosticRef: string;
  readonly version: number;
  readonly state: TurnDegradationState | null;
  readonly reasonCode: string;
  readonly allowedLayer: TurnDegradationAllowedLayer;
  readonly allowedClasses: readonly TurnDegradationAllowedClass[];
  readonly transportStatus: TurnTransportStatus | 'unobserved';
  readonly transportReasonCode: string;
  readonly recovery: 'stable' | 'pending';
  readonly localAction: 'unbound' | 'applying' | 'applied' | 'failed';
  readonly localReasonCode: string;
}

export interface TurnDegradationEnvironment {
  readonly maxReceivers: number;
  nowMs(): number;
  setTimer(callback: () => void, delayMs: number): ReturnType<typeof setTimeout>;
  clearTimer(timer: ReturnType<typeof setTimeout>): void;
}

export const TURN_DEGRADATION_ENVIRONMENT = new InjectionToken<TurnDegradationEnvironment>(
  'TURN_DEGRADATION_ENVIRONMENT',
  {
    providedIn: 'root',
    factory: () => ({
      maxReceivers: 256,
      nowMs: () => Date.now(),
      setTimer: (callback, delayMs) => globalThis.setTimeout(callback, delayMs),
      clearTimer: timer => globalThis.clearTimeout(timer),
    }),
  },
);

interface ReceiverRecord {
  readonly generation: number;
  readonly binding: TurnDegradationReceiverBinding;
  hub: TurnDegradationHubStateDocument | null;
  transportStatus: TurnTransportStatus | 'unobserved';
  transportReasonCode: string;
  lastTransportAtMs: number;
  recoveryRequired: boolean;
  healthyObservations: number;
  firstHealthyAtMs: number;
  recoveryTimer: ReturnType<typeof setTimeout> | null;
  localAction: TurnDegradationReceiverView['localAction'];
  localReasonCode: string;
}

const RECEIVER_REF = /^trd1\.[a-f0-9]{24}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const SIGNATURE = /^[a-f0-9]{64}$/;
const STATES = new Set<TurnDegradationState>([
  'direct', 'relay_ok', 'relay_capped', 'control_only', 'fallback', 'rejected',
]);
const HEALTHY_STATES = new Set<TurnDegradationState>(['direct', 'relay_ok']);
const LAYERS = new Set<TurnDegradationAllowedLayer>(['high', 'low', 'none']);
const CLASSES = new Set<TurnDegradationAllowedClass>(['control', 'key', 'transcript', 'media']);
const HUB_REASON_CODES = new Set([
  'turn_direct_path_available',
  'turn_relay_available',
  'turn_relay_lower_cap',
  'turn_relay_control_only',
  'turn_parent_fallback_required',
  'turn_credential_expired',
  'turn_relay_unavailable',
  'turn_encryption_requirement_unmet',
  'turn_retry_budget_exhausted_fallback',
  'turn_retry_budget_exhausted',
]);
const TRANSPORT_REASON_CODES = new Set([
  'turn_transport_healthy',
  'turn_transport_degraded',
  'turn_transport_unavailable',
  'turn_transport_network_changed',
]);
const HEALTHY_OBSERVATIONS_REQUIRED = 3;
const RECOVERY_DWELL_MS = 3_000;
const TRANSPORT_OBSERVATION_AGE_MS = 10_000;
const MAX_SCHEDULED_RECOVERY_MS = 60_000;

/**
 * Receiver-scoped browser read model. Hub documents remain authoritative;
 * transport observations can delay recovery but can never create a state.
 */
@Injectable({ providedIn: 'root' })
export class TurnDegradationStateService implements OnDestroy {
  private readonly receivers = new Map<string, ReceiverRecord>();
  private readonly viewsSubject = new BehaviorSubject<readonly TurnDegradationReceiverView[]>(
    Object.freeze([]),
  );
  private generation = 0;

  readonly state$ = this.viewsSubject.asObservable();

  constructor(
    @Inject(TURN_DEGRADATION_ENVIRONMENT) private readonly environment: TurnDegradationEnvironment,
    private readonly layers: SfuBroadcastReceiverLayerControllerService,
  ) {
    if (!Number.isSafeInteger(environment.maxReceivers) || environment.maxReceivers < 1) {
      throw new Error('turn_degradation_configuration_invalid');
    }
  }

  bind(binding: TurnDegradationReceiverBinding): () => void {
    validateBinding(binding);
    const previous = this.receivers.get(binding.receiverDiagnosticRef);
    if (!previous && this.receivers.size >= this.environment.maxReceivers) {
      throw new Error('turn_degradation_receiver_capacity_exceeded');
    }
    if (previous) this.remove(previous);
    const record: ReceiverRecord = {
      generation: ++this.generation,
      binding,
      hub: null,
      transportStatus: 'unobserved',
      transportReasonCode: 'turn_transport_unobserved',
      lastTransportAtMs: -1,
      recoveryRequired: false,
      healthyObservations: 0,
      firstHealthyAtMs: 0,
      recoveryTimer: null,
      localAction: 'unbound',
      localReasonCode: 'turn_degradation_state_unobserved',
    };
    this.receivers.set(binding.receiverDiagnosticRef, record);
    this.publish();
    return () => {
      if (this.receivers.get(binding.receiverDiagnosticRef)?.generation === record.generation) {
        this.remove(record);
        this.publish();
      }
    };
  }

  observeHubState(observation: HubValidatedTurnDegradationObservation): boolean {
    const document = normalizeHubState(observation);
    const record = this.receivers.get(document.receiver_diagnostic_ref);
    if (!record) return false;
    if (record.hub && document.version < record.hub.version) return false;
    if (record.hub && document.version === record.hub.version) {
      if (JSON.stringify(document) === JSON.stringify(record.hub)) return false;
      throw new Error('turn_degradation_version_conflict');
    }

    record.hub = document;
    this.resetRecoveryEvidence(record);
    if (HEALTHY_STATES.has(document.state) && record.recoveryRequired) {
      record.localAction = 'applying';
      record.localReasonCode = 'turn_degradation_recovery_pending';
    } else if (HEALTHY_STATES.has(document.state)) {
      this.apply(record);
    } else {
      record.recoveryRequired = true;
      this.apply(record);
    }
    this.publish();
    return true;
  }

  observeTransport(observation: TurnTransportObservation): boolean {
    validateTransportObservation(observation, this.environment.nowMs());
    const record = this.receivers.get(observation.receiverDiagnosticRef);
    if (!record || observation.observedAtMs <= record.lastTransportAtMs) return false;
    record.lastTransportAtMs = observation.observedAtMs;
    record.transportStatus = observation.status;
    record.transportReasonCode = observation.reasonCode;

    if (!record.hub || !HEALTHY_STATES.has(record.hub.state) || !record.recoveryRequired) {
      this.publish();
      return true;
    }
    if (observation.status !== 'healthy') {
      this.resetRecoveryEvidence(record);
      record.localAction = 'applying';
      record.localReasonCode = 'turn_degradation_recovery_pending';
      this.publish();
      return true;
    }
    if (record.healthyObservations === 0) record.firstHealthyAtMs = observation.observedAtMs;
    record.healthyObservations = Math.min(
      HEALTHY_OBSERVATIONS_REQUIRED,
      record.healthyObservations + 1,
    );
    this.tryRecover(record);
    this.publish();
    return true;
  }

  snapshot(receiverDiagnosticRef: string): TurnDegradationReceiverView | null {
    const record = this.receivers.get(receiverDiagnosticRef);
    return record ? view(record) : null;
  }

  clearAll(): void {
    for (const record of [...this.receivers.values()]) this.remove(record);
    this.publish();
  }

  ngOnDestroy(): void {
    this.clearAll();
    this.viewsSubject.complete();
  }

  private tryRecover(record: ReceiverRecord): void {
    const document = record.hub;
    if (
      !document
      || !HEALTHY_STATES.has(document.state)
      || !record.recoveryRequired
      || record.transportStatus !== 'healthy'
      || record.healthyObservations < HEALTHY_OBSERVATIONS_REQUIRED
    ) return;
    const targetMs = Math.max(
      record.firstHealthyAtMs + RECOVERY_DWELL_MS,
      document.cooldown_until_seconds * 1_000,
    );
    const delayMs = targetMs - this.environment.nowMs();
    if (delayMs <= 0) {
      record.recoveryRequired = false;
      this.resetRecoveryEvidence(record);
      this.apply(record);
      return;
    }
    if (record.recoveryTimer !== null || delayMs > MAX_SCHEDULED_RECOVERY_MS) return;
    const generation = record.generation;
    const version = document.version;
    record.recoveryTimer = this.environment.setTimer(() => {
      record.recoveryTimer = null;
      if (
        this.receivers.get(record.binding.receiverDiagnosticRef)?.generation !== generation
        || record.hub?.version !== version
      ) return;
      this.tryRecover(record);
      this.publish();
    }, delayMs);
  }

  private apply(record: ReceiverRecord): void {
    const document = record.hub;
    if (!document) return;
    const generation = record.generation;
    const version = document.version;
    const classes = new Set(document.allowed_classes);
    try {
      record.binding.speech?.applyTurnAllowedClasses(classes);
      const mediaAllowed = classes.has('media') && document.allowed_layer !== 'none';
      if (!mediaAllowed) {
        record.binding.subscriptions.setRemotePublicationSubscribed(record.binding.publicationRef, false);
      } else if (document.allowed_layer === 'low') {
        record.localAction = 'applying';
        record.localReasonCode = 'turn_degradation_local_action_applying';
        record.binding.subscriptions.setRemotePublicationSubscribed(record.binding.publicationRef, false);
        void this.layers.applyMinimum(
          record.binding.subscriptionRef,
          document.reason_code,
        ).then(applied => {
          if (
            this.receivers.get(record.binding.receiverDiagnosticRef)?.generation !== generation
            || record.hub?.version !== version
          ) return;
          if (applied) {
            record.binding.subscriptions.setRemotePublicationSubscribed(record.binding.publicationRef, true);
            record.localAction = 'applied';
            record.localReasonCode = 'turn_degradation_local_action_applied';
          } else {
            this.failClosed(record);
          }
          this.publish();
        }).catch(() => {
          if (this.receivers.get(record.binding.receiverDiagnosticRef)?.generation !== generation) return;
          this.failClosed(record);
          this.publish();
        });
        return;
      } else {
        record.binding.subscriptions.setRemotePublicationSubscribed(record.binding.publicationRef, true);
      }
      if (document.state === 'fallback') record.binding.onParentFallback?.(document.reason_code);
      if (document.state === 'rejected') record.binding.onRejected?.(document.reason_code);
      record.localAction = 'applied';
      record.localReasonCode = 'turn_degradation_local_action_applied';
    } catch {
      this.failClosed(record);
    }
  }

  private failClosed(record: ReceiverRecord): void {
    try { record.binding.speech?.applyTurnAllowedClasses(new Set()); } catch { /* fail closed below */ }
    try {
      record.binding.subscriptions.setRemotePublicationSubscribed(record.binding.publicationRef, false);
    } catch { /* state still records a bounded failure */ }
    record.localAction = 'failed';
    record.localReasonCode = 'turn_degradation_local_action_failed';
  }

  private resetRecoveryEvidence(record: ReceiverRecord): void {
    if (record.recoveryTimer !== null) this.environment.clearTimer(record.recoveryTimer);
    record.recoveryTimer = null;
    record.healthyObservations = 0;
    record.firstHealthyAtMs = 0;
  }

  private remove(record: ReceiverRecord): void {
    this.resetRecoveryEvidence(record);
    this.failClosed(record);
    this.receivers.delete(record.binding.receiverDiagnosticRef);
  }

  private publish(): void {
    this.viewsSubject.next(Object.freeze(
      [...this.receivers.values()]
        .sort((left, right) => left.binding.receiverDiagnosticRef.localeCompare(right.binding.receiverDiagnosticRef))
        .map(view),
    ));
  }
}

function validateBinding(binding: TurnDegradationReceiverBinding): void {
  if (
    !RECEIVER_REF.test(binding.receiverDiagnosticRef)
    || !ID.test(binding.subscriptionRef)
    || !ID.test(binding.publicationRef)
    || typeof binding.subscriptions?.setRemotePublicationSubscribed !== 'function'
    || (binding.speech !== undefined && typeof binding.speech.applyTurnAllowedClasses !== 'function')
  ) throw new Error('turn_degradation_binding_invalid');
}

function normalizeHubState(
  observation: HubValidatedTurnDegradationObservation,
): TurnDegradationHubStateDocument {
  if (observation?.validation !== 'hub-turn-degradation-state-accepted-v1') {
    throw new Error('turn_degradation_state_unvalidated');
  }
  const value = observation.document;
  const classes = Array.isArray(value?.allowed_classes) ? [...value.allowed_classes] : [];
  if (
    !value
    || !RECEIVER_REF.test(value.receiver_diagnostic_ref)
    || !Number.isSafeInteger(value.version) || value.version < 1
    || !STATES.has(value.state)
    || !HUB_REASON_CODES.has(value.reason_code)
    || !LAYERS.has(value.allowed_layer)
    || classes.length > CLASSES.size
    || classes.some(item => !CLASSES.has(item))
    || new Set(classes).size !== classes.length
    || !Number.isSafeInteger(value.retry_count) || value.retry_count < 0
    || !Number.isSafeInteger(value.cooldown_until_seconds) || value.cooldown_until_seconds < 0
    || !SIGNATURE.test(value.signature)
  ) throw new Error('turn_degradation_state_invalid');
  return Object.freeze({
    receiver_diagnostic_ref: value.receiver_diagnostic_ref,
    version: value.version,
    state: value.state,
    reason_code: value.reason_code,
    allowed_layer: value.allowed_layer,
    allowed_classes: Object.freeze(classes),
    retry_count: value.retry_count,
    cooldown_until_seconds: value.cooldown_until_seconds,
    signature: value.signature,
  });
}

function validateTransportObservation(observation: TurnTransportObservation, nowMs: number): void {
  if (
    observation?.source !== 'transport'
    || !RECEIVER_REF.test(observation.receiverDiagnosticRef)
    || !['healthy', 'degraded', 'unavailable'].includes(observation.status)
    || !TRANSPORT_REASON_CODES.has(observation.reasonCode)
    || !Number.isSafeInteger(observation.observedAtMs)
    || observation.observedAtMs > nowMs + 1_000
    || observation.observedAtMs < nowMs - TRANSPORT_OBSERVATION_AGE_MS
  ) throw new Error('turn_degradation_transport_observation_invalid');
}

function view(record: ReceiverRecord): TurnDegradationReceiverView {
  const document = record.hub;
  return Object.freeze({
    receiverDiagnosticRef: record.binding.receiverDiagnosticRef,
    version: document?.version ?? 0,
    state: document?.state ?? null,
    reasonCode: document?.reason_code ?? 'turn_degradation_state_unobserved',
    allowedLayer: document?.allowed_layer ?? 'none',
    allowedClasses: Object.freeze([...(document?.allowed_classes ?? [])]),
    transportStatus: record.transportStatus,
    transportReasonCode: record.transportReasonCode,
    recovery: record.recoveryRequired ? 'pending' : 'stable',
    localAction: record.localAction,
    localReasonCode: record.localReasonCode,
  });
}
