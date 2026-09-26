import { PlayerId, UnitKind } from './model';

interface ActionContext { readonly player: PlayerId; readonly turn: number }
export type GameAction = ActionContext & (
  | { readonly type: 'MOVE'; readonly unitId: string; readonly to: string }
  | { readonly type: 'ATTACK'; readonly unitId: string; readonly targetId: string }
  | { readonly type: 'RECRUIT'; readonly kind: UnitKind; readonly to: string }
  | { readonly type: 'END_TURN' }
  | { readonly type: 'NAGABANDA_CHECK'; readonly unitId: string }
);

export type RuleErrorCode = 'invalid_action' | 'game_over' | 'wrong_player' | 'stale_turn'
  | 'unknown_unit' | 'not_your_unit' | 'unknown_cell' | 'impassable' | 'not_adjacent'
  | 'occupied' | 'not_enough_ap' | 'deva_move_attack' | 'already_attacked' | 'exhausted'
  | 'invalid_target' | 'not_at_ashram' | 'not_enough_soma';

export interface RuleError { readonly code: RuleErrorCode; readonly message: string }
export type ValidationResult = { readonly ok: true; readonly action: GameAction }
  | { readonly ok: false; readonly error: RuleError };

export function reject(code: RuleErrorCode, message: string): ValidationResult & { ok: false } {
  return { ok: false, error: { code, message } };
}

// Closed wire format: imported actions never introduce new rule or state fields.
export function parseAction(value: unknown): GameAction | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const a = value as Record<string, unknown>;
  if ((a.player !== 'sun' && a.player !== 'moon') || !Number.isSafeInteger(a.turn) || Number(a.turn) < 1) return null;
  const context: ActionContext = { player: a.player, turn: a.turn as number };
  const isId = (id: unknown): id is string => typeof id === 'string' && id.length > 0 && id.length <= 64;
  const closed = (...fields: string[]) => Object.keys(a).every(key => ['type', 'player', 'turn', ...fields].includes(key));
  switch (a.type) {
    case 'MOVE':
      return closed('unitId', 'to') && isId(a.unitId) && isId(a.to)
        ? { ...context, type: a.type, unitId: a.unitId, to: a.to } : null;
    case 'ATTACK':
      return closed('unitId', 'targetId') && isId(a.unitId) && isId(a.targetId)
        ? { ...context, type: a.type, unitId: a.unitId, targetId: a.targetId } : null;
    case 'RECRUIT':
      return closed('kind', 'to') && (a.kind === 'naga' || a.kind === 'rishi' || a.kind === 'deva') && isId(a.to)
        ? { ...context, type: a.type, kind: a.kind as UnitKind, to: a.to } : null;
    case 'END_TURN':
      return closed() ? { ...context, type: a.type } : null;
    case 'NAGABANDA_CHECK':
      return closed('unitId') && isId(a.unitId) ? { ...context, type: a.type, unitId: a.unitId } : null;
    default: return null;
  }
}
