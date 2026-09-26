import { GameState, Unit, UNIT_RULES } from './model';
import { nagabanda, rishiEscapeCell } from './nagabanda';

export interface CombatOutcome {
  readonly result: 'escaped' | 'attacker_wins' | 'defender_wins' | 'standoff';
  readonly attackerStrength: number;
  readonly defenderStrength: number;
  readonly escapeTo: string | null;
}

export function combatStrength(state: GameState, unit: Unit, defending = false): number {
  const shelter = defending && state.ashrams.some(ashram => ashram.owner === unit.owner && ashram.cellId === unit.cellId) ? 1 : 0;
  return Math.max(0, UNIT_RULES[unit.kind].strength + shelter - (nagabanda(state, unit).level === 'full' ? 1 : 0));
}

// Called with a validated adjacent pair. No randomness, clocks or external services.
export function resolveCombat(state: GameState, attacker: Unit, defender: Unit): CombatOutcome {
  const attackerStrength = combatStrength(state, attacker);
  const defenderStrength = combatStrength(state, defender, true);
  const escapeTo = rishiEscapeCell(state, defender);
  const result = escapeTo ? 'escaped' : attackerStrength > defenderStrength ? 'attacker_wins'
    : attackerStrength < defenderStrength ? 'defender_wins' : 'standoff';
  return { result, attackerStrength, defenderStrength, escapeTo };
}
