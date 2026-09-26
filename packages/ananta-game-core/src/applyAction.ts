import { GameState, RULES, UNIT_RULES, opponent } from './model';
import { RuleError } from './actions';
import { validateAction } from './validateAction';
import { CombatOutcome, resolveCombat } from './combat';
import { NagabandaStatus, nagabanda } from './nagabanda';

export type ActionResult = { readonly ok: false; readonly state: GameState; readonly error: RuleError }
  | { readonly ok: true; readonly state: GameState; readonly combat?: CombatOutcome; readonly nagabanda?: NagabandaStatus };

function checkVictory(state: GameState): GameState {
  const captured = state.ashrams.find(ashram => state.units.some(unit => unit.cellId === ashram.cellId && unit.owner !== ashram.owner));
  return captured ? { ...state, winner: opponent(captured.owner) } : state;
}

function endTurn(state: GameState): GameState {
  if (state.activePlayer === 'moon' && state.round >= RULES.maxRounds) return { ...state, winner: 'draw' };
  const activePlayer = opponent(state.activePlayer);
  const income = state.turn < 2 ? 0 : state.ashrams.filter(ashram => ashram.owner === activePlayer).length * RULES.somaPerAshram;
  return {
    ...state, activePlayer, turn: state.turn + 1, round: state.round + (activePlayer === 'sun' ? 1 : 0),
    ap: RULES.apPerTurn, soma: { ...state.soma, [activePlayer]: state.soma[activePlayer] + income },
    units: state.units.map(unit => unit.owner === activePlayer
      ? { ...unit, moved: false, attacked: false, fled: false, exhausted: false } : unit),
  };
}

export function applyAction(state: GameState, input: unknown): ActionResult {
  const validation = validateAction(state, input);
  if (validation.ok === false) return { ok: false, state, error: validation.error };
  const action = validation.action;
  switch (action.type) {
    case 'END_TURN': return { ok: true, state: endTurn(state) };
    case 'NAGABANDA_CHECK':
      return { ok: true, state, nagabanda: nagabanda(state, state.units.find(unit => unit.id === action.unitId)!) };
    case 'MOVE':
      return { ok: true, state: checkVictory({ ...state, ap: state.ap - RULES.moveAp,
        units: state.units.map(unit => unit.id === action.unitId ? { ...unit, cellId: action.to, moved: true } : unit),
      }) };
    case 'RECRUIT':
      return { ok: true, state: { ...state, ap: state.ap - RULES.recruitAp, nextUnitId: state.nextUnitId + 1,
        soma: { ...state.soma, [action.player]: state.soma[action.player] - UNIT_RULES[action.kind].somaCost },
        units: [...state.units, { id: `${action.player}-${state.nextUnitId}`, owner: action.player, kind: action.kind,
          cellId: action.to, moved: false, attacked: false, fled: false, exhausted: true }],
      } };
    case 'ATTACK': {
      const attacker = state.units.find(unit => unit.id === action.unitId)!;
      const defender = state.units.find(unit => unit.id === action.targetId)!;
      const combat = resolveCombat(state, attacker, defender);
      const units = state.units.filter(unit => !(combat.result === 'attacker_wins' && unit.id === defender.id)
        && !(combat.result === 'defender_wins' && unit.id === attacker.id)).map(unit => {
        if (unit.id === attacker.id) return { ...unit, attacked: true,
          cellId: combat.result === 'attacker_wins' || combat.result === 'escaped' ? defender.cellId : unit.cellId };
        if (unit.id === defender.id && combat.escapeTo) return { ...unit, cellId: combat.escapeTo, fled: true };
        return unit;
      });
      return { ok: true, combat, state: checkVictory({ ...state, units, ap: state.ap - RULES.attackAp }) };
    }
  }
}
