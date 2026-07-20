import { describe, expect, it } from 'vitest';
import {
  SFU_COOLDOWN_MS,
  initialSemanticMediaTransportState,
  reduceSemanticMediaTransport,
  type SemanticMediaTransportSignals,
} from './semantic-media-transport-state-machine';

const healthy = (nowMs: number): SemanticMediaTransportSignals => ({
  nowMs, enabled: true, capability: 'supported', admitted: true, sfuHealthy: true,
  e2eeReady: true, qualityHealthy: true, userPreference: 'auto', revoked: false,
});

describe('semantic media transport state machine', () => {
  it('activates only after admission and three healthy windows', () => {
    let state = initialSemanticMediaTransportState(0);
    state = reduceSemanticMediaTransport(state, healthy(1));
    expect(state.mode).toBe('sfu_connecting');
    state = reduceSemanticMediaTransport(state, healthy(2));
    expect(state.mode).toBe('sfu_connecting');
    state = reduceSemanticMediaTransport(state, healthy(3));
    expect(state.mode).toBe('sfu_active');
  });

  it('drains before ordinary fallback and observes cooldown', () => {
    let state = initialSemanticMediaTransportState(0);
    for (const now of [1, 2, 3]) state = reduceSemanticMediaTransport(state, healthy(now));
    state = reduceSemanticMediaTransport(state, { ...healthy(4), qualityHealthy: false });
    state = reduceSemanticMediaTransport(state, { ...healthy(5), qualityHealthy: false });
    expect(state.mode).toBe('sfu_draining');
    expect(state.sfuBulkEnabled || state.ordinaryBulkEnabled).toBe(false);
    state = reduceSemanticMediaTransport(state, healthy(6));
    expect(state.mode).toBe('ordinary_cooldown');
    state = reduceSemanticMediaTransport(state, healthy(6 + SFU_COOLDOWN_MS - 1));
    expect(state.mode).toBe('ordinary_cooldown');
  });

  it.each([
    { capability: 'unknown' as const, reason: 'capability_unknown' },
    { capability: 'unsupported' as const, reason: 'capability_unknown' },
  ])('unknown/unsupported capability deterministically stays ordinary', row => {
    const state = reduceSemanticMediaTransport(initialSemanticMediaTransportState(0), {
      ...healthy(1), capability: row.capability,
    });
    expect(state.mode).toBe('ordinary');
    expect(state.reasonCode).toBe(row.reason);
  });

  it('is overlap-safe under duplicate and reordered-like randomized signals', () => {
    let state = initialSemanticMediaTransportState(0);
    let seed = 89123;
    for (let index = 1; index <= 2_000; index += 1) {
      seed = (seed * 48271) % 0x7fffffff;
      const signal = healthy(index * 10);
      const randomized = {
        ...signal,
        enabled: (seed & 1) === 0,
        admitted: (seed & 2) === 0,
        sfuHealthy: (seed & 4) === 0,
        e2eeReady: (seed & 8) === 0,
        qualityHealthy: (seed & 16) === 0,
        revoked: (seed & 32) !== 0,
      };
      state = reduceSemanticMediaTransport(state, randomized);
      expect(state.ordinaryBulkEnabled && state.sfuBulkEnabled).toBe(false);
    }
  });

  it('does not count duplicate or reordered health samples toward hysteresis', () => {
    let state = reduceSemanticMediaTransport(initialSemanticMediaTransportState(0), healthy(10));
    expect(state.healthyWindows).toBe(1);
    state = reduceSemanticMediaTransport(state, healthy(10));
    state = reduceSemanticMediaTransport(state, healthy(9));
    expect(state.mode).toBe('sfu_connecting');
    expect(state.healthyWindows).toBe(1);
    state = reduceSemanticMediaTransport(state, healthy(11));
    expect(state.healthyWindows).toBe(2);
  });
});
