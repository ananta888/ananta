import { classifyVisualMotion, OrderedVisualMetrics } from './semantic-motion-policy';
import { SemanticReferencePolicy } from './semantic-reference-policy';

const BASE: OrderedVisualMetrics = {
  changedRatio: 0, meanDelta: 0, coherentMotionRatio: 0,
  verticalMotion: 0, edgeContinuity: 1, driftScore: 0, sampleCount: 10,
};

describe('semantic motion and reference policies', () => {
  it.each([
    ['static', { changedRatio: 0, meanDelta: 0 }],
    ['local_change', { changedRatio: 0.1, meanDelta: 10 }],
    ['motion', { changedRatio: 0.3, meanDelta: 30, coherentMotionRatio: 0.7 }],
    ['text_scroll', { changedRatio: 0.3, meanDelta: 30, coherentMotionRatio: 0.8, verticalMotion: 0.2, edgeContinuity: 0.9 }],
    ['scene_cut', { changedRatio: 0.8, meanDelta: 80 }],
    ['drift', { driftScore: 0.2 }],
    ['unknown', { changedRatio: 0.5, meanDelta: 20 }],
  ])('classifies %s with fixed ordered thresholds', (expected, patch) => {
    expect(classifyVisualMotion({ ...BASE, ...patch }).classification).toBe(expected);
  });

  it('is deterministic for identical ordered metrics and synthetic clock', () => {
    const classification = classifyVisualMotion({ ...BASE, changedRatio: 0.1, meanDelta: 10 });
    const policy = new SemanticReferencePolicy(undefined, () => 10_000);
    const input = {
      classification, referenceCreatedAtMs: 9_000, deltaChainLength: 0,
      pendingBurstBytes: 0, recoveryTimestampsMs: [],
    };
    expect(policy.decide(input)).toEqual(policy.decide(input));
    expect(policy.decide(input)).toMatchObject({ action: 'region_repair', reasonCode: 'local_change_repair' });
  });

  it.each([
    ['scene_cut_requires_reference', { classification: classifyVisualMotion({ ...BASE, changedRatio: 0.8, meanDelta: 80 }) }],
    ['reference_expired', { referenceCreatedAtMs: 1 }],
    ['delta_chain_limit', { deltaChainLength: 24 }],
    ['burst_budget_exceeded', { pendingBurstBytes: 400 * 1024 }],
    ['recovery_rate_exceeded', { recoveryTimestampsMs: [9001, 9002, 9003] }],
  ])('enforces %s', (reason, patch) => {
    const policy = new SemanticReferencePolicy(undefined, () => 10_000);
    const decision = policy.decide({
      classification: classifyVisualMotion(BASE), referenceCreatedAtMs: 9_000,
      deltaChainLength: 0, pendingBurstBytes: 0, recoveryTimestampsMs: [], ...patch,
    });
    expect(decision.reasonCode).toBe(reason);
  });
});
