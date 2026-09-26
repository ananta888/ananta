import { GameState, PlayerId, Unit } from './model';
import { neighbors, unitAt } from './board';

export interface NagabandaStatus {
  readonly level: 'none' | 'partial' | 'full';
  readonly blocked: readonly string[];
  readonly neighbors: readonly string[];
  readonly protectedByAshram: boolean;
}

export function enemyNagaInfluence(state: GameState, cellId: string, owner: PlayerId): boolean {
  return neighbors(state, cellId).some(cell => {
    const unit = unitAt(state, cell.id);
    return unit?.kind === 'naga' && unit.owner !== owner;
  });
}

export function nagabanda(state: GameState, unit: Unit): NagabandaStatus {
  const adjacent = neighbors(state, unit.cellId);
  const blocked = adjacent.filter(cell => {
    const occupant = unitAt(state, cell.id);
    return !cell.passable || (occupant && occupant.owner !== unit.owner)
      || enemyNagaInfluence(state, cell.id, unit.owner);
  }).map(cell => cell.id);
  const protectedByAshram = state.ashrams.some(ashram => ashram.owner === unit.owner
    && (ashram.cellId === unit.cellId || adjacent.some(cell => cell.id === ashram.cellId)));
  return {
    level: blocked.length === 0 ? 'none'
      : !protectedByAshram && blocked.length === adjacent.length ? 'full' : 'partial',
    blocked, neighbors: adjacent.map(cell => cell.id), protectedByAshram,
  };
}

export function rishiEscapeCell(state: GameState, unit: Unit): string | null {
  if (unit.kind !== 'rishi' || unit.fled || unit.exhausted || nagabanda(state, unit).level === 'full') return null;
  const cells = neighbors(state, unit.cellId).filter(cell => cell.passable && !unitAt(state, cell.id)
    && !enemyNagaInfluence(state, cell.id, unit.owner)
    && !state.ashrams.some(ashram => ashram.cellId === cell.id && ashram.owner !== unit.owner));
  return cells.map(cell => cell.id).sort()[0] ?? null;
}
