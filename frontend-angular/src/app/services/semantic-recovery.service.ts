import { SemanticVisualFallbackService } from './semantic-visual-fallback.service';

export type SemanticRecoveryTrigger =
  | 'drift' | 'scene_cut' | 'chunk_loss' | 'lease_loss' | 'deadline' | 'validator_failure';
export type SemanticRecoveryAction = 'region_repair' | 'reference_refresh' | 'ordinary_fallback' | 'cooldown_hold';

export interface RecoveryDecision {
  readonly action: SemanticRecoveryAction;
  readonly reasonCode: string;
  readonly trigger: SemanticRecoveryTrigger;
  readonly attempt: number;
}

interface TriggerState { attempts: number; windowStartedMs: number; lastAttemptMs: number }

const BUDGETS: Readonly<Record<SemanticRecoveryTrigger, Readonly<{ repairs: number; refreshes: number }>>> = Object.freeze({
  drift: { repairs: 1, refreshes: 1 },
  scene_cut: { repairs: 0, refreshes: 1 },
  chunk_loss: { repairs: 2, refreshes: 1 },
  lease_loss: { repairs: 0, refreshes: 0 },
  deadline: { repairs: 1, refreshes: 1 },
  validator_failure: { repairs: 0, refreshes: 1 },
});

export class SemanticRecoveryService {
  private readonly states = new Map<string, Map<SemanticRecoveryTrigger, TriggerState>>();

  constructor(
    private readonly fallback: SemanticVisualFallbackService = new SemanticVisualFallbackService(),
    private readonly cooldownMs = 500,
    private readonly windowMs = 10_000,
  ) {}

  recover(receiverId: string, trigger: SemanticRecoveryTrigger, nowMs: number): RecoveryDecision {
    if (!receiverId || !Number.isSafeInteger(nowMs) || !(trigger in BUDGETS)) {
      this.fallback.forceOrdinary(nowMs);
      return Object.freeze({ action: 'ordinary_fallback', reasonCode: 'invalid_recovery_context', trigger, attempt: 0 });
    }
    const receiver = this.states.get(receiverId) ?? new Map<SemanticRecoveryTrigger, TriggerState>();
    this.states.set(receiverId, receiver);
    let state = receiver.get(trigger);
    if (!state || nowMs - state.windowStartedMs > this.windowMs) {
      state = { attempts: 0, windowStartedMs: nowMs, lastAttemptMs: Number.NEGATIVE_INFINITY };
      receiver.set(trigger, state);
    }
    if (nowMs - state.lastAttemptMs < this.cooldownMs) {
      return Object.freeze({ action: 'cooldown_hold', reasonCode: `${trigger}_cooldown`, trigger, attempt: state.attempts });
    }
    state.lastAttemptMs = nowMs;
    state.attempts += 1;
    const budget = BUDGETS[trigger];
    if (state.attempts <= budget.repairs) {
      return Object.freeze({ action: 'region_repair', reasonCode: `${trigger}_region_repair`, trigger, attempt: state.attempts });
    }
    if (state.attempts <= budget.repairs + budget.refreshes) {
      return Object.freeze({ action: 'reference_refresh', reasonCode: `${trigger}_reference_refresh`, trigger, attempt: state.attempts });
    }
    this.fallback.forceOrdinary(nowMs);
    return Object.freeze({ action: 'ordinary_fallback', reasonCode: `${trigger}_ordinary_fallback`, trigger, attempt: state.attempts });
  }

  clear(receiverId: string): void { this.states.delete(receiverId); }
}
