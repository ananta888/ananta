export type PlayerId = 'sun' | 'moon';
export type UnitKind = 'naga' | 'rishi' | 'deva';
export type Loka = 'upper' | 'middle' | 'lower';

export interface HexCell {
  readonly id: string;
  readonly q: number;
  readonly r: number;
  readonly loka: Loka;
  readonly passable: boolean;
}

export interface Unit {
  readonly id: string;
  readonly owner: PlayerId;
  readonly kind: UnitKind;
  readonly cellId: string;
  readonly moved: boolean;
  readonly attacked: boolean;
  readonly fled: boolean;
  readonly exhausted: boolean;
}

export interface Ashram {
  readonly id: string;
  readonly owner: PlayerId;
  readonly cellId: string;
}

export interface GameState {
  readonly ruleset: 'brutal-mvp-v1';
  readonly cells: readonly HexCell[];
  readonly units: readonly Unit[];
  readonly ashrams: readonly Ashram[];
  readonly soma: Readonly<Record<PlayerId, number>>;
  readonly activePlayer: PlayerId;
  readonly round: number;
  readonly turn: number;
  readonly ap: number;
  readonly nextUnitId: number;
  readonly winner: PlayerId | 'draw' | null;
}

export const RULES = Object.freeze({
  apPerTurn: 6,
  startingSoma: 4,
  somaPerAshram: 2,
  maxRounds: 40,
  moveAp: 1,
  attackAp: 2,
  recruitAp: 2,
});

export const UNIT_RULES: Readonly<Record<UnitKind, Readonly<{ strength: number; somaCost: number }>>> = Object.freeze({
  naga: Object.freeze({ strength: 2, somaCost: 2 }),
  rishi: Object.freeze({ strength: 1, somaCost: 2 }),
  deva: Object.freeze({ strength: 3, somaCost: 4 }),
});

export function opponent(player: PlayerId): PlayerId {
  return player === 'sun' ? 'moon' : 'sun';
}
