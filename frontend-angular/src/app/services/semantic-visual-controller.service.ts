import { Injectable } from '@angular/core';

export const SEMANTIC_VISUAL_CONTROLLER_POLICY = 'semantic-visual-controller/1.0.0' as const;

export interface SemanticVisualAuthorityEvidence {
  readonly captureCapability: boolean;
  readonly contractId: string | null;
  readonly contractActive: boolean;
  readonly contractExpiresAtMs: number;
  readonly leaseId: string | null;
  readonly leaseActive: boolean;
  readonly leaseExpiresAtMs: number;
  readonly qualityReportId: string | null;
  readonly qualityReportPassed: boolean;
  readonly qualityReportExpiresAtMs: number;
}

export interface SemanticVisualAdaptationMetrics {
  readonly byteRatio: number;
  readonly cpuRatio: number;
  readonly workingBytes: number;
  readonly qualityScore: number;
  readonly driftScore: number;
}

export interface SemanticVisualControllerInput {
  readonly receiverId: string;
  readonly nowMs: number;
  readonly requestedMode: 'observe_only' | 'active' | 'ordinary';
  readonly releaseGatePassed: boolean;
  readonly userOrdinaryOverride: boolean;
  readonly authority: Readonly<SemanticVisualAuthorityEvidence>;
  readonly metrics: Readonly<SemanticVisualAdaptationMetrics>;
}

export interface SemanticVisualControllerDecision {
  readonly policyVersion: typeof SEMANTIC_VISUAL_CONTROLLER_POLICY;
  readonly mode: 'observe_only' | 'semantic' | 'ordinary';
  readonly referenceIntervalMs: number;
  readonly residualBudgetBytes: number;
  readonly reasonCode: string;
}

interface ControllerState { goodSamples: number; lastFallbackMs: number }

@Injectable({ providedIn: 'root' })
export class SemanticVisualControllerService {
  private readonly states = new Map<string, ControllerState>();

  decide(input: Readonly<SemanticVisualControllerInput>): SemanticVisualControllerDecision {
    const state = this.states.get(input.receiverId) ?? { goodSamples: 0, lastFallbackMs: Number.NEGATIVE_INFINITY };
    this.states.set(input.receiverId, state);
    if (!validInput(input)) return this.fallback(state, input.nowMs, 'controller_input_invalid');
    if (input.userOrdinaryOverride || input.requestedMode === 'ordinary') {
      return this.fallback(state, input.nowMs, 'user_ordinary_override');
    }
    if (input.requestedMode === 'observe_only') {
      state.goodSamples = 0;
      return decision('observe_only', 5000, 0, 'observe_only_requested');
    }
    // The controller consumes evidence only. It cannot mint a gate, capability,
    // contract, lease, or validator result when any authority field is absent.
    if (!input.releaseGatePassed) return this.fallback(state, input.nowMs, 'visual_release_gate_closed');
    const authority = input.authority;
    if (!authority.captureCapability || !authority.contractId || !authority.contractActive
        || authority.contractExpiresAtMs <= input.nowMs || !authority.leaseId || !authority.leaseActive
        || authority.leaseExpiresAtMs <= input.nowMs || !authority.qualityReportId
        || !authority.qualityReportPassed || authority.qualityReportExpiresAtMs <= input.nowMs) {
      return this.fallback(state, input.nowMs, 'visual_authority_evidence_missing');
    }
    const metric = input.metrics;
    const good = metric.byteRatio <= 0.7 && metric.cpuRatio <= 2 && metric.workingBytes <= 64 * 1024 * 1024
      && metric.qualityScore >= 0.8 && metric.driftScore <= 0.1;
    const severe = metric.byteRatio > 1.25 || metric.cpuRatio > 3 || metric.workingBytes > 128 * 1024 * 1024
      || metric.qualityScore < 0.65 || metric.driftScore > 0.2;
    if (severe) return this.fallback(state, input.nowMs, 'visual_quality_or_resource_no_go');
    if (!good) {
      state.goodSamples = 0;
      return decision('observe_only', 3000, 32 * 1024, 'visual_metrics_conditional');
    }
    if (input.nowMs - state.lastFallbackMs < 5000) return decision('ordinary', 5000, 0, 'visual_reentry_cooldown');
    state.goodSamples += 1;
    if (state.goodSamples < 3) return decision('observe_only', 3000, 32 * 1024, 'visual_hysteresis_collecting');
    const interval = metric.byteRatio <= 0.4 && metric.driftScore <= 0.03 ? 5000 : 3000;
    const residual = metric.byteRatio <= 0.4 ? 96 * 1024 : 48 * 1024;
    return decision('semantic', interval, residual, 'visual_quality_and_resource_gate_passed');
  }

  reset(receiverId: string): void { this.states.delete(receiverId); }

  private fallback(state: ControllerState, nowMs: number, reasonCode: string): SemanticVisualControllerDecision {
    state.goodSamples = 0;
    if (Number.isSafeInteger(nowMs)) state.lastFallbackMs = nowMs;
    return decision('ordinary', 5000, 0, reasonCode);
  }
}

function validInput(input: SemanticVisualControllerInput): boolean {
  const metrics = Object.values(input.metrics);
  return Boolean(input.receiverId) && Number.isSafeInteger(input.nowMs)
    && metrics.every(value => Number.isFinite(value) && value >= 0)
    && input.metrics.qualityScore <= 1 && input.metrics.driftScore <= 1;
}
function decision(
  mode: SemanticVisualControllerDecision['mode'], referenceIntervalMs: number,
  residualBudgetBytes: number, reasonCode: string,
): SemanticVisualControllerDecision {
  return Object.freeze({ policyVersion: SEMANTIC_VISUAL_CONTROLLER_POLICY, mode, referenceIntervalMs, residualBudgetBytes, reasonCode });
}
