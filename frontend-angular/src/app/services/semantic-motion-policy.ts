export const SEMANTIC_MOTION_POLICY_VERSION = 'semantic-motion-policy/1.0.0' as const;

export type VisualChangeClass =
  | 'static'
  | 'local_change'
  | 'motion'
  | 'text_scroll'
  | 'scene_cut'
  | 'drift'
  | 'unknown';

export interface OrderedVisualMetrics {
  readonly changedRatio: number;
  readonly meanDelta: number;
  readonly coherentMotionRatio: number;
  readonly verticalMotion: number;
  readonly edgeContinuity: number;
  readonly driftScore: number;
  readonly sampleCount: number;
}

export interface MotionClassification {
  readonly policyVersion: typeof SEMANTIC_MOTION_POLICY_VERSION;
  readonly classification: VisualChangeClass;
  readonly reasonCode: string;
}

/** Pure, ordered threshold policy. Order is part of the versioned contract. */
export function classifyVisualMotion(metrics: Readonly<OrderedVisualMetrics>): MotionClassification {
  const values = Object.values(metrics);
  if (values.some(value => !Number.isFinite(value)) || !Number.isSafeInteger(metrics.sampleCount)
      || metrics.sampleCount < 1 || metrics.changedRatio < 0 || metrics.changedRatio > 1
      || metrics.coherentMotionRatio < 0 || metrics.coherentMotionRatio > 1
      || metrics.edgeContinuity < 0 || metrics.edgeContinuity > 1 || metrics.driftScore < 0) {
    return result('unknown', 'motion_metrics_invalid');
  }
  if (metrics.driftScore >= 0.18) return result('drift', 'drift_threshold_exceeded');
  if (metrics.changedRatio >= 0.72 && metrics.meanDelta >= 64) return result('scene_cut', 'scene_cut_global_delta');
  if (metrics.changedRatio <= 0.005 && metrics.meanDelta <= 2) return result('static', 'change_below_static_threshold');
  if (metrics.coherentMotionRatio >= 0.7 && Math.abs(metrics.verticalMotion) >= 0.08
      && metrics.edgeContinuity >= 0.75) return result('text_scroll', 'coherent_vertical_edge_motion');
  if (metrics.coherentMotionRatio >= 0.55 && metrics.changedRatio >= 0.08) return result('motion', 'coherent_motion_detected');
  if (metrics.changedRatio <= 0.25) return result('local_change', 'bounded_local_delta');
  return result('unknown', 'change_pattern_unclassified');
}

function result(classification: VisualChangeClass, reasonCode: string): MotionClassification {
  return Object.freeze({ policyVersion: SEMANTIC_MOTION_POLICY_VERSION, classification, reasonCode });
}
