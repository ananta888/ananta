import { MotionClassification, VisualChangeClass } from './semantic-motion-policy';

export const SEMANTIC_REFERENCE_POLICY_VERSION = 'semantic-reference-policy/1.0.0' as const;

export interface ReferencePolicyLimits {
  readonly maxReferenceAgeMs: number;
  readonly maxDeltaChain: number;
  readonly maxBurstBytes: number;
  readonly maxRecoveriesPerWindow: number;
  readonly recoveryWindowMs: number;
}

export const DEFAULT_REFERENCE_LIMITS: Readonly<ReferencePolicyLimits> = Object.freeze({
  maxReferenceAgeMs: 5_000,
  maxDeltaChain: 24,
  maxBurstBytes: 384 * 1024,
  maxRecoveriesPerWindow: 3,
  recoveryWindowMs: 10_000,
});

export interface ReferencePolicyInput {
  readonly classification: MotionClassification;
  readonly referenceCreatedAtMs: number | null;
  readonly deltaChainLength: number;
  readonly pendingBurstBytes: number;
  readonly recoveryTimestampsMs: readonly number[];
}

export type ReferenceAction = 'hold' | 'delta' | 'region_repair' | 'new_reference' | 'ordinary_fallback';

export interface ReferenceDecision {
  readonly policyVersion: typeof SEMANTIC_REFERENCE_POLICY_VERSION;
  readonly classification: VisualChangeClass;
  readonly action: ReferenceAction;
  readonly reasonCode: string;
}

/** Pure reference planner: it never encodes, renders, transports, or schedules. */
export class SemanticReferencePolicy {
  constructor(
    private readonly limits: Readonly<ReferencePolicyLimits> = DEFAULT_REFERENCE_LIMITS,
    private readonly clock: () => number = () => Date.now(),
  ) {}

  decide(input: Readonly<ReferencePolicyInput>): ReferenceDecision {
    const now = this.clock();
    if (!Number.isSafeInteger(now) || input.referenceCreatedAtMs !== null && !Number.isSafeInteger(input.referenceCreatedAtMs)
        || !Number.isSafeInteger(input.deltaChainLength) || input.deltaChainLength < 0
        || !Number.isSafeInteger(input.pendingBurstBytes) || input.pendingBurstBytes < 0
        || input.recoveryTimestampsMs.some(value => !Number.isSafeInteger(value))) {
      return this.result(input.classification.classification, 'ordinary_fallback', 'reference_metrics_invalid');
    }
    const recentRecoveries = input.recoveryTimestampsMs.filter(
      value => value <= now && now - value <= this.limits.recoveryWindowMs,
    ).length;
    if (recentRecoveries >= this.limits.maxRecoveriesPerWindow) {
      return this.result(input.classification.classification, 'ordinary_fallback', 'recovery_rate_exceeded');
    }
    if (input.classification.classification === 'scene_cut') {
      return this.result('scene_cut', 'new_reference', 'scene_cut_requires_reference');
    }
    if (input.referenceCreatedAtMs === null) {
      return this.result(input.classification.classification, 'new_reference', 'reference_missing');
    }
    if (now - input.referenceCreatedAtMs > this.limits.maxReferenceAgeMs) {
      return this.result(input.classification.classification, 'new_reference', 'reference_expired');
    }
    if (input.deltaChainLength >= this.limits.maxDeltaChain) {
      return this.result(input.classification.classification, 'new_reference', 'delta_chain_limit');
    }
    if (input.pendingBurstBytes > this.limits.maxBurstBytes) {
      return this.result(input.classification.classification, 'ordinary_fallback', 'burst_budget_exceeded');
    }
    switch (input.classification.classification) {
      case 'static': return this.result('static', 'hold', 'static_reference_reusable');
      case 'local_change': return this.result('local_change', 'region_repair', 'local_change_repair');
      case 'motion': return this.result('motion', 'delta', 'bounded_motion_delta');
      case 'text_scroll': return this.result('text_scroll', 'delta', 'text_scroll_delta');
      case 'drift': return this.result('drift', 'new_reference', 'drift_requires_reference');
      default: return this.result('unknown', 'new_reference', 'unknown_requires_reference');
    }
  }

  private result(classification: VisualChangeClass, action: ReferenceAction, reasonCode: string): ReferenceDecision {
    return Object.freeze({ policyVersion: SEMANTIC_REFERENCE_POLICY_VERSION, classification, action, reasonCode });
  }
}
