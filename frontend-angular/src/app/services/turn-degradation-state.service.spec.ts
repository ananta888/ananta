import { describe, expect, it, vi } from 'vitest';

import type { SfuBroadcastReceiverLayerControllerService } from './sfu-broadcast-receiver-layer-controller.service';
import type { SfuSubscriptionLayerPort } from './sfu-room-session.ports';
import {
  TurnDegradationStateService,
  type HubValidatedTurnDegradationObservation,
  type TurnDegradationAllowedClass,
  type TurnDegradationEnvironment,
  type TurnDegradationReceiverBinding,
  type TurnDegradationState,
} from './turn-degradation-state.service';

const RECEIVER_A = `trd1.${'a'.repeat(24)}`;
const RECEIVER_B = `trd1.${'b'.repeat(24)}`;

function environment(maxReceivers = 4): TurnDegradationEnvironment & {
  now: number;
  pendingTimers(): number;
} {
  let nextId = 0;
  const timers = new Map<number, ReturnType<typeof setTimeout>>();
  const value = {
    maxReceivers,
    now: 1_800_000_000_000,
    nowMs: () => value.now,
    setTimer: (_callback: () => void, _delayMs: number) => {
      const id = ++nextId as unknown as ReturnType<typeof setTimeout>;
      timers.set(Number(id), id);
      return id;
    },
    clearTimer: (timer: ReturnType<typeof setTimeout>) => { timers.delete(Number(timer)); },
    pendingTimers: () => timers.size,
  };
  return value;
}

function hub(
  receiverDiagnosticRef: string,
  version: number,
  state: TurnDegradationState,
  allowedLayer: 'high' | 'low' | 'none',
  allowedClasses: readonly TurnDegradationAllowedClass[],
  signature = 'c'.repeat(64),
): HubValidatedTurnDegradationObservation {
  const reasons: Record<TurnDegradationState, string> = {
    direct: 'turn_direct_path_available',
    relay_ok: 'turn_relay_available',
    relay_capped: 'turn_relay_lower_cap',
    control_only: 'turn_relay_control_only',
    fallback: 'turn_parent_fallback_required',
    rejected: 'turn_relay_unavailable',
  };
  return {
    validation: 'hub-turn-degradation-state-accepted-v1',
    document: {
      receiver_diagnostic_ref: receiverDiagnosticRef,
      version,
      state,
      reason_code: reasons[state],
      allowed_layer: allowedLayer,
      allowed_classes: allowedClasses,
      retry_count: state === 'direct' || state === 'relay_ok' ? 0 : 1,
      cooldown_until_seconds: 0,
      signature,
    },
  };
}

function receiver(receiverDiagnosticRef: string): TurnDegradationReceiverBinding & {
  readonly subscribed: ReturnType<typeof vi.fn>;
  readonly speechClasses: ReturnType<typeof vi.fn>;
} {
  const subscribed = vi.fn();
  const speechClasses = vi.fn();
  const subscriptions: SfuSubscriptionLayerPort = {
    layerControlMode: 'manual_quality',
    applyRemoteSubscriptions: () => undefined,
    setRemotePublicationSubscribed: subscribed,
  };
  return {
    receiverDiagnosticRef,
    subscriptionRef: `subscription-${receiverDiagnosticRef.at(-1)}`,
    publicationRef: `publication-${receiverDiagnosticRef.at(-1)}`,
    subscriptions,
    speech: { applyTurnAllowedClasses: speechClasses },
    subscribed,
    speechClasses,
  };
}

function service(env = environment()) {
  const applyMinimum = vi.fn(async () => true);
  return {
    env,
    applyMinimum,
    value: new TurnDegradationStateService(
      env,
      { applyMinimum } as unknown as SfuBroadcastReceiverLayerControllerService,
    ),
  };
}

describe('TurnDegradationStateService', () => {
  it('applies a Hub cap only to the bound receiver through existing SFU and speech ports', async () => {
    const { value, applyMinimum } = service();
    const first = receiver(RECEIVER_A);
    const second = receiver(RECEIVER_B);
    value.bind(first);
    value.bind(second);

    expect(value.observeHubState(hub(RECEIVER_A, 1, 'relay_capped', 'low', [
      'control', 'key', 'transcript', 'media',
    ]))).toBe(true);
    await Promise.resolve();

    expect(applyMinimum).toHaveBeenCalledWith('subscription-a', 'turn_relay_lower_cap');
    expect(first.subscribed.mock.calls).toEqual([['publication-a', false], ['publication-a', true]]);
    expect(first.speechClasses).toHaveBeenCalledOnce();
    expect([...first.speechClasses.mock.calls[0][0]]).toEqual(['control', 'key', 'transcript', 'media']);
    expect(second.subscribed).not.toHaveBeenCalled();
    expect(value.snapshot(RECEIVER_A)?.state).toBe('relay_capped');
    expect(value.snapshot(RECEIVER_B)?.state).toBeNull();
  });

  it('never derives authority from transport and opens recovery only after stable healthy evidence', () => {
    const { value, env } = service();
    const binding = receiver(RECEIVER_A);
    value.bind(binding);
    value.observeHubState(hub(RECEIVER_A, 1, 'control_only', 'none', ['control', 'key']));
    expect(binding.subscribed).toHaveBeenLastCalledWith('publication-a', false);

    env.now += 100;
    value.observeTransport({
      source: 'transport', receiverDiagnosticRef: RECEIVER_A, status: 'healthy',
      reasonCode: 'turn_transport_healthy', observedAtMs: env.now,
    });
    expect(value.snapshot(RECEIVER_A)?.state).toBe('control_only');

    value.observeHubState(hub(RECEIVER_A, 2, 'direct', 'high', [
      'control', 'key', 'transcript', 'media',
    ], 'd'.repeat(64)));
    expect(value.snapshot(RECEIVER_A)?.recovery).toBe('pending');
    expect(binding.subscribed).not.toHaveBeenLastCalledWith('publication-a', true);

    for (const advance of [100, 1_000, 2_000]) {
      env.now += advance;
      value.observeTransport({
        source: 'transport', receiverDiagnosticRef: RECEIVER_A, status: 'healthy',
        reasonCode: 'turn_transport_healthy', observedAtMs: env.now,
      });
    }
    expect(value.snapshot(RECEIVER_A)?.state).toBe('direct');
    expect(value.snapshot(RECEIVER_A)?.recovery).toBe('stable');
    expect(binding.subscribed).toHaveBeenLastCalledWith('publication-a', true);
  });

  it('bounds cardinality, rejects same-version conflicts and cleans receiver state fail-closed', () => {
    const env = environment(1);
    const { value } = service(env);
    const first = receiver(RECEIVER_A);
    const release = value.bind(first);
    expect(() => value.bind(receiver(RECEIVER_B))).toThrow('turn_degradation_receiver_capacity_exceeded');
    expect(value.observeHubState(hub(RECEIVER_A, 1, 'relay_ok', 'high', ['media']))).toBe(true);
    expect(value.observeHubState(hub(RECEIVER_A, 1, 'relay_ok', 'high', ['media']))).toBe(false);
    expect(() => value.observeHubState(
      hub(RECEIVER_A, 1, 'relay_ok', 'high', ['media'], 'e'.repeat(64)),
    )).toThrow('turn_degradation_version_conflict');

    release();
    expect(value.snapshot(RECEIVER_A)).toBeNull();
    expect(first.subscribed).toHaveBeenLastCalledWith('publication-a', false);
    expect([...first.speechClasses.mock.calls.at(-1)[0]]).toEqual([]);
    expect(env.pendingTimers()).toBe(0);
  });
});
