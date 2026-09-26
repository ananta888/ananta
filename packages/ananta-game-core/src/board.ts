import { GameState, HexCell, RULES, Unit, UnitKind, PlayerId } from './model';

export function hexDistance(a: HexCell, b: HexCell): number {
  return Math.max(Math.abs(a.q - b.q), Math.abs(a.r - b.r), Math.abs(a.q + a.r - b.q - b.r));
}

export function neighbors(state: GameState, cellId: string): readonly HexCell[] {
  const origin = state.cells.find(cell => cell.id === cellId);
  return origin ? state.cells.filter(cell => hexDistance(origin, cell) === 1) : [];
}

export function unitAt(state: GameState, cellId: string): Unit | undefined {
  return state.units.find(unit => unit.cellId === cellId);
}

export function createBoard(): HexCell[] {
  const cells: HexCell[] = [];
  for (let r = -2; r <= 2; r++) {
    for (let q = Math.max(-2, -r - 2); q <= Math.min(2, -r + 2); q++) {
      cells.push({
        id: `H${String(cells.length + 1).padStart(2, '0')}`, q, r,
        loka: r < 0 ? 'upper' : r > 0 ? 'lower' : 'middle', passable: true,
      });
    }
  }
  return cells;
}

function initialUnit(owner: PlayerId, kind: UnitKind, cellId: string): Unit {
  return { id: `${owner}-${kind}`, owner, kind, cellId, moved: false, attacked: false, fled: false, exhausted: false };
}

export function createInitialState(): GameState {
  return {
    ruleset: 'brutal-mvp-v1', cells: createBoard(),
    units: [
      initialUnit('sun', 'naga', 'H04'), initialUnit('sun', 'rishi', 'H01'), initialUnit('sun', 'deva', 'H05'),
      initialUnit('moon', 'naga', 'H16'), initialUnit('moon', 'rishi', 'H19'), initialUnit('moon', 'deva', 'H15'),
    ],
    ashrams: [{ id: 'ashram-sun', owner: 'sun', cellId: 'H01' }, { id: 'ashram-moon', owner: 'moon', cellId: 'H19' }],
    soma: { sun: RULES.startingSoma, moon: RULES.startingSoma },
    activePlayer: 'sun', round: 1, turn: 1, ap: RULES.apPerTurn, nextUnitId: 7, winner: null,
  };
}
