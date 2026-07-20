export type SemanticMediaBulkMode = 'ordinary' | 'sfu_connecting' | 'sfu_active' | 'sfu_draining' | 'ordinary_cooldown';
export type SemanticMediaTransportReason =
  | 'sfu_disabled' | 'capability_unknown' | 'sfu_unhealthy' | 'e2ee_unavailable'
  | 'quality_breach' | 'user_selected_ordinary' | 'sfu_admitted' | 'sfu_ready'
  | 'sfu_revoked' | 'cooldown_elapsed' | 'transition_pending';

export interface SemanticMediaTransportSignals {
  readonly nowMs: number;
  readonly enabled: boolean;
  readonly capability: 'supported' | 'unsupported' | 'unknown';
  readonly admitted: boolean;
  readonly sfuHealthy: boolean;
  readonly e2eeReady: boolean;
  readonly qualityHealthy: boolean;
  readonly userPreference: 'auto' | 'ordinary' | 'sfu';
  readonly revoked: boolean;
}

export interface SemanticMediaTransportState {
  readonly mode: SemanticMediaBulkMode;
  readonly enteredAtMs: number;
  readonly lastSignalAtMs: number;
  readonly healthyWindows: number;
  readonly unhealthyWindows: number;
  readonly reasonCode: SemanticMediaTransportReason;
}

export interface SemanticMediaTransportDecision extends SemanticMediaTransportState {
  readonly ordinaryBulkEnabled: boolean;
  readonly sfuBulkEnabled: boolean;
}

export const SFU_ACTIVATION_HEALTHY_WINDOWS = 3;
export const SFU_QUALITY_BREACH_WINDOWS = 2;
export const SFU_COOLDOWN_MS = 15_000;
export const SFU_CONNECT_DEADLINE_MS = 10_000;

export interface SemanticMediaTransportPolicyPort {
  initial(nowMs?: number): SemanticMediaTransportDecision;
  reduce(
    state: Readonly<SemanticMediaTransportState>,
    signal: Readonly<SemanticMediaTransportSignals>,
  ): SemanticMediaTransportDecision;
}

export const DEFAULT_SEMANTIC_MEDIA_TRANSPORT_POLICY: SemanticMediaTransportPolicyPort = Object.freeze({
  initial: initialSemanticMediaTransportState,
  reduce: reduceSemanticMediaTransport,
});

/** Pure Hub-signal driven state machine; at most one bulk path can be active. */
export function reduceSemanticMediaTransport(
  state: Readonly<SemanticMediaTransportState>,
  signal: Readonly<SemanticMediaTransportSignals>,
): SemanticMediaTransportDecision {
  if (!Number.isSafeInteger(signal.nowMs) || signal.nowMs < 0) {
    throw new Error('semantic_transport_clock_invalid');
  }
  if (signal.nowMs <= state.lastSignalAtMs) return repeatedDecision(state);
  const fallback = fallbackReason(signal);
  if (fallback !== null) {
    if (state.mode === 'sfu_active' || state.mode === 'sfu_connecting') {
      return decision('sfu_draining', signal.nowMs, signal.nowMs, 0, state.unhealthyWindows, fallback);
    }
    if (state.mode === 'sfu_draining') {
      return decision('ordinary_cooldown', signal.nowMs, signal.nowMs, 0, 0, fallback);
    }
    if (state.mode === 'ordinary_cooldown' && signal.nowMs - state.enteredAtMs < SFU_COOLDOWN_MS) {
      return decision('ordinary_cooldown', state.enteredAtMs, signal.nowMs, 0, 0, fallback);
    }
    return decision('ordinary', signal.nowMs, signal.nowMs, 0, 0, fallback);
  }
  if (state.mode === 'ordinary' || state.mode === 'ordinary_cooldown') {
    if (state.mode === 'ordinary_cooldown' && signal.nowMs - state.enteredAtMs < SFU_COOLDOWN_MS) {
      return decision('ordinary_cooldown', state.enteredAtMs, signal.nowMs, 0, 0, state.reasonCode);
    }
    return signal.admitted
      ? decision('sfu_connecting', signal.nowMs, signal.nowMs, signal.sfuHealthy && signal.e2eeReady ? 1 : 0, 0, 'sfu_admitted')
      : decision('ordinary', state.mode === 'ordinary' ? state.enteredAtMs : signal.nowMs,
        signal.nowMs, 0, 0, 'transition_pending');
  }
  if (state.mode === 'sfu_connecting') {
    if (!signal.admitted || signal.nowMs - state.enteredAtMs >= SFU_CONNECT_DEADLINE_MS) {
      return decision('sfu_draining', signal.nowMs, signal.nowMs, 0, 0, 'sfu_unhealthy');
    }
    const healthy = signal.sfuHealthy && signal.e2eeReady ? state.healthyWindows + 1 : 0;
    if (healthy >= SFU_ACTIVATION_HEALTHY_WINDOWS) {
      return decision('sfu_active', signal.nowMs, signal.nowMs, healthy, 0, 'sfu_ready');
    }
    return decision('sfu_connecting', state.enteredAtMs, signal.nowMs, healthy, 0, 'sfu_admitted');
  }
  if (state.mode === 'sfu_active') {
    const unhealthy = signal.sfuHealthy && signal.qualityHealthy ? 0 : state.unhealthyWindows + 1;
    if (unhealthy >= SFU_QUALITY_BREACH_WINDOWS) {
      return decision('sfu_draining', signal.nowMs, signal.nowMs, 0, unhealthy, 'quality_breach');
    }
    return decision('sfu_active', state.enteredAtMs, signal.nowMs, state.healthyWindows, unhealthy, 'sfu_ready');
  }
  // Draining disables SFU first. Ordinary becomes active on the next reducer
  // pass, making overlap impossible even with duplicated/reordered signals.
  return decision('ordinary_cooldown', signal.nowMs, signal.nowMs, 0, 0, state.reasonCode);
}

export function initialSemanticMediaTransportState(nowMs = 0): SemanticMediaTransportDecision {
  return decision('ordinary', nowMs, nowMs, 0, 0, 'sfu_disabled');
}

function fallbackReason(signal: SemanticMediaTransportSignals): SemanticMediaTransportReason | null {
  if (!signal.enabled) return 'sfu_disabled';
  if (signal.userPreference === 'ordinary') return 'user_selected_ordinary';
  if (signal.capability !== 'supported') return 'capability_unknown';
  if (signal.revoked) return 'sfu_revoked';
  if (!signal.e2eeReady) return 'e2ee_unavailable';
  if (!signal.admitted && !signal.sfuHealthy) return 'sfu_unhealthy';
  return null;
}

function decision(
  mode: SemanticMediaBulkMode,
  enteredAtMs: number,
  lastSignalAtMs: number,
  healthyWindows: number,
  unhealthyWindows: number,
  reasonCode: SemanticMediaTransportReason,
): SemanticMediaTransportDecision {
  return Object.freeze({
    mode, enteredAtMs, lastSignalAtMs, healthyWindows, unhealthyWindows, reasonCode,
    ordinaryBulkEnabled: mode === 'ordinary' || mode === 'ordinary_cooldown',
    sfuBulkEnabled: mode === 'sfu_active',
  });
}

function repeatedDecision(state: Readonly<SemanticMediaTransportState>): SemanticMediaTransportDecision {
  return decision(
    state.mode,
    state.enteredAtMs,
    state.lastSignalAtMs,
    state.healthyWindows,
    state.unhealthyWindows,
    state.reasonCode,
  );
}
