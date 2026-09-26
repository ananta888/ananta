import { GameState, RULES, UNIT_RULES } from './model';
import { parseAction, reject, ValidationResult } from './actions';
import { neighbors, unitAt } from './board';

export function validateAction(state: GameState, input: unknown): ValidationResult {
  const action = parseAction(input);
  if (!action) return reject('invalid_action', 'Die Aktion hat ein ungültiges Format.');
  if (state.winner) return reject('game_over', 'Die Partie ist beendet.');
  if (action.player !== state.activePlayer) return reject('wrong_player', 'Der andere Spieler ist am Zug.');
  if (action.turn !== state.turn) return reject('stale_turn', 'Diese Aktion gehört zu einem anderen Zug.');
  const accepted = { ok: true as const, action };
  if (action.type === 'END_TURN') return accepted;
  if (action.type === 'NAGABANDA_CHECK') {
    return state.units.some(unit => unit.id === action.unitId) ? accepted : reject('unknown_unit', 'Die Figur existiert nicht.');
  }

  const cost = action.type === 'MOVE' ? RULES.moveAp : action.type === 'ATTACK' ? RULES.attackAp : RULES.recruitAp;
  if (state.ap < cost) return reject('not_enough_ap', 'Nicht genügend Aktionspunkte.');

  if (action.type === 'RECRUIT' || action.type === 'MOVE') {
    const cell = state.cells.find(candidate => candidate.id === action.to);
    if (!cell) return reject('unknown_cell', 'Das Zielfeld existiert nicht.');
    if (!cell.passable) return reject('impassable', 'Das Zielfeld ist unpassierbar.');
    if (unitAt(state, cell.id)) return reject('occupied', 'Das Zielfeld ist besetzt.');
  }

  if (action.type === 'RECRUIT') {
    const atOwnAshram = state.ashrams.some(ashram => ashram.owner === action.player
      && (ashram.cellId === action.to || neighbors(state, ashram.cellId).some(cell => cell.id === action.to)));
    const atEnemyAshram = state.ashrams.some(ashram => ashram.owner !== action.player && ashram.cellId === action.to);
    if (!atOwnAshram || atEnemyAshram) return reject('not_at_ashram', 'Rekrutierung ist nur am eigenen Ashram und seinen Nachbarfeldern erlaubt.');
    if (state.soma[action.player] < UNIT_RULES[action.kind].somaCost) return reject('not_enough_soma', 'Nicht genügend Soma.');
    return accepted;
  }

  const unit = state.units.find(candidate => candidate.id === action.unitId);
  if (!unit) return reject('unknown_unit', 'Die Figur existiert nicht.');
  if (unit.owner !== action.player) return reject('not_your_unit', 'Diese Figur gehört dem anderen Spieler.');
  if (unit.exhausted) return reject('exhausted', 'Neue Figuren werden erst im nächsten eigenen Zug aktiv.');
  if (unit.kind === 'deva' && (action.type === 'MOVE' ? unit.attacked : unit.moved)) {
    return reject('deva_move_attack', 'Deva darf im selben Zug entweder bewegen oder angreifen.');
  }
  const adjacent = neighbors(state, unit.cellId);
  if (action.type === 'MOVE') {
    return adjacent.some(cell => cell.id === action.to) ? accepted : reject('not_adjacent', 'Bewegung ist nur auf ein Nachbarfeld möglich.');
  }
  if (unit.attacked) return reject('already_attacked', 'Diese Figur hat in diesem Zug bereits angegriffen.');
  const target = state.units.find(candidate => candidate.id === action.targetId);
  if (!target || target.owner === action.player) return reject('invalid_target', 'Wähle eine gegnerische Figur.');
  return adjacent.some(cell => cell.id === target.cellId) ? accepted : reject('not_adjacent', 'Angriffe sind nur auf Nachbarfelder möglich.');
}
