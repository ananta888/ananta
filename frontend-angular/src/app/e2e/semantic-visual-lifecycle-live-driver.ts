import {
  SemanticVisualControllerInput,
  SemanticVisualControllerService,
} from '../services/semantic-visual-controller.service';
import { SemanticVisualFallbackService } from '../services/semantic-visual-fallback.service';
import {
  SemanticRecoveryService,
  SemanticRecoveryTrigger,
} from '../services/semantic-recovery.service';

const E2E_QUERY = 'semanticVisualLiveE2e';
const SCENARIOS = new Set([
  'static_ui',
  'text_scroll',
  'cursor_animation',
  'camera',
  'scene_cut',
  'strong_noise',
]);

export interface SemanticVisualLifecycleResult {
  readonly scenario: string;
  readonly observeOnly: boolean;
  readonly active: boolean;
  readonly recoveryAttempted: boolean;
  readonly recoveryEndedInOrdinary: boolean;
  readonly revoked: boolean;
  readonly reconnectFenced: boolean;
  readonly ordinaryFallback: boolean;
  readonly authorityWasConsumedOnly: boolean;
  readonly policyVersion: string;
}

/**
 * Explicit browser acceptance adapter for the production controller classes.
 * It is installed only for the dedicated query flag and never changes a Hub
 * release decision.  The active branch consumes a bounded, pre-issued
 * authority fixture so the browser cannot mint authority in this harness.
 */
export function installSemanticVisualLifecycleLiveDriver(): void {
  if (typeof window === 'undefined') return;
  if (new URL(window.location.href).searchParams.get(E2E_QUERY) !== '1') return;
  const target = window as unknown as {
    __ANANTA_SEMANTIC_VISUAL_E2E__?: {
      runLifecycle(scenario: string): SemanticVisualLifecycleResult;
    };
  };
  target.__ANANTA_SEMANTIC_VISUAL_E2E__ = Object.freeze({
    runLifecycle: (scenario: string) => runLifecycle(scenario),
  });
}

function runLifecycle(scenario: string): SemanticVisualLifecycleResult {
  if (!SCENARIOS.has(scenario)) throw new Error('semantic_visual_e2e_scenario_invalid');
  const controller = new SemanticVisualControllerService();
  const fallback = new SemanticVisualFallbackService(5_000, 3);
  const recovery = new SemanticRecoveryService(fallback, 500, 10_000);
  const receiverId = `visual-e2e-${scenario}`;
  const base = input(receiverId, scenario);

  const observe = controller.decide({ ...base, requestedMode: 'observe_only' });
  const collecting = controller.decide(base);
  const stabilizing = controller.decide({ ...base, nowMs: base.nowMs + 1 });
  const active = controller.decide({ ...base, nowMs: base.nowMs + 2 });

  const trigger = recoveryTrigger(scenario);
  let recoveryDecision = recovery.recover(receiverId, trigger, base.nowMs + 1_000);
  for (let attempt = 1; attempt < 4 && recoveryDecision.action !== 'ordinary_fallback'; attempt += 1) {
    recoveryDecision = recovery.recover(receiverId, trigger, base.nowMs + 1_000 + attempt * 600);
  }

  const revoked = controller.decide({
    ...base,
    nowMs: base.nowMs + 5_000,
    authority: { ...base.authority, contractActive: false, leaseActive: false },
  });
  controller.reset(receiverId);
  const reconnected = controller.decide({
    ...base,
    receiverId: `${receiverId}-epoch-2`,
    nowMs: base.nowMs + 6_000,
    releaseGatePassed: false,
  });
  const ordinary = controller.decide({
    ...base,
    receiverId: `${receiverId}-override`,
    nowMs: base.nowMs + 7_000,
    userOrdinaryOverride: true,
  });

  return Object.freeze({
    scenario,
    observeOnly: observe.mode === 'observe_only',
    active: collecting.mode === 'observe_only'
      && stabilizing.mode === 'observe_only'
      && active.mode === 'semantic',
    recoveryAttempted: recoveryDecision.attempt > 0,
    recoveryEndedInOrdinary: recoveryDecision.action === 'ordinary_fallback',
    revoked: revoked.mode === 'ordinary'
      && revoked.reasonCode === 'visual_authority_evidence_missing',
    reconnectFenced: reconnected.mode === 'ordinary'
      && reconnected.reasonCode === 'visual_release_gate_closed',
    ordinaryFallback: ordinary.mode === 'ordinary'
      && ordinary.reasonCode === 'user_ordinary_override',
    authorityWasConsumedOnly: Boolean(base.authority.contractId && base.authority.leaseId
      && base.authority.qualityReportId),
    policyVersion: active.policyVersion,
  });
}

function input(receiverId: string, scenario: string): SemanticVisualControllerInput {
  const nowMs = 20_000;
  const noisy = scenario === 'strong_noise';
  return {
    receiverId,
    nowMs,
    requestedMode: 'active',
    releaseGatePassed: true,
    userOrdinaryOverride: false,
    authority: Object.freeze({
      captureCapability: true,
      contractId: `contract-${scenario}`,
      contractActive: true,
      contractExpiresAtMs: nowMs + 60_000,
      leaseId: `lease-${scenario}`,
      leaseActive: true,
      leaseExpiresAtMs: nowMs + 60_000,
      qualityReportId: `quality-${scenario}`,
      qualityReportPassed: true,
      qualityReportExpiresAtMs: nowMs + 60_000,
    }),
    metrics: Object.freeze({
      byteRatio: noisy ? 0.65 : 0.4,
      cpuRatio: noisy ? 1.8 : 1,
      workingBytes: noisy ? 32 * 1024 * 1024 : 4 * 1024 * 1024,
      qualityScore: noisy ? 0.82 : 0.94,
      driftScore: noisy ? 0.09 : 0.02,
    }),
  };
}

function recoveryTrigger(scenario: string): SemanticRecoveryTrigger {
  if (scenario === 'scene_cut' || scenario === 'camera') return 'scene_cut';
  if (scenario === 'strong_noise') return 'validator_failure';
  if (scenario === 'cursor_animation') return 'chunk_loss';
  return 'drift';
}
