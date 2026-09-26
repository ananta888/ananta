const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
  createInitialState, createBoard, neighbors, unitAt, hexDistance, applyAction, validateAction,
  nagabanda, rishiEscapeCell, resolveCombat, createSession, recordAction, exportReplay, importReplay,
  RULES, MAX_REPLAY_ACTIONS, MAX_REPLAY_CHARACTERS,
} = require('../dist');

function unit(id, owner, kind, cellId, extra = {}) {
  return { id, owner, kind, cellId, moved: false, attacked: false, fled: false, exhausted: false, ...extra };
}
function position(units, extra = {}) { return { ...createInitialState(), units, ashrams: [], ...extra }; }
function action(state, type, fields = {}) { return { type, player: state.activePlayer, turn: state.turn, ...fields }; }
function act(state, type, fields = {}) {
  const result = applyAction(state, action(state, type, fields));
  assert.equal(result.ok, true, result.error?.message);
  return result.state;
}
function rejected(state, type, fields, code) {
  const before = JSON.stringify(state);
  const result = applyAction(state, action(state, type, fields));
  assert.equal(result.ok, false);
  assert.equal(result.error.code, code);
  assert.equal(result.state, state);
  assert.equal(JSON.stringify(state), before);
}
function freeze(value) {
  if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}

test('19 unique axial cells, symmetric starting armies, three Lokas and correct edge adjacency', () => {
  const state = createInitialState();
  assert.equal(state.cells.length, 19);
  assert.equal(new Set(state.cells.map(cell => cell.id)).size, 19);
  assert.deepEqual([...new Set(state.cells.map(cell => cell.loka))], ['upper', 'middle', 'lower']);
  assert.equal(neighbors(state, 'H10').length, 6);
  assert.equal(neighbors(state, 'H01').length, 3);
  assert.deepEqual(neighbors(state, 'missing'), []);
  for (const cell of state.cells) {
    assert.ok(Math.max(Math.abs(cell.q), Math.abs(cell.r), Math.abs(cell.q + cell.r)) <= 2);
    for (const adjacent of neighbors(state, cell.id)) assert.equal(hexDistance(cell, adjacent), 1);
  }
  for (const sun of state.units.filter(unit => unit.owner === 'sun')) {
    const moon = state.units.find(unit => unit.owner === 'moon' && unit.kind === sun.kind);
    const a = state.cells.find(cell => cell.id === sun.cellId);
    const b = state.cells.find(cell => cell.id === moon.cellId);
    assert.equal(a.q + b.q, 0); assert.equal(a.r + b.r, 0);
  }
  assert.deepEqual(state, JSON.parse(JSON.stringify(state)));
  assert.notEqual(state.cells, createInitialState().cells);
});

test('legal movement consumes one AP and never mutates even a deeply frozen input', () => {
  const state = freeze(createInitialState());
  const next = act(state, 'MOVE', { unitId: 'sun-naga', to: 'H09' });
  assert.equal(next.ap, 5);
  assert.equal(unitAt(next, 'H09').id, 'sun-naga');
  assert.equal(unitAt(state, 'H04').id, 'sun-naga');
});

test('movement rejects occupied, nonadjacent, missing, impassable, foreign and exhausted targets', () => {
  const state = createInitialState();
  rejected(state, 'MOVE', { unitId: 'sun-naga', to: 'H05' }, 'occupied');
  rejected(state, 'MOVE', { unitId: 'sun-naga', to: 'H17' }, 'not_adjacent');
  rejected(state, 'MOVE', { unitId: 'sun-naga', to: 'missing' }, 'unknown_cell');
  rejected(state, 'MOVE', { unitId: 'missing', to: 'H09' }, 'unknown_unit');
  rejected(state, 'MOVE', { unitId: 'moon-naga', to: 'H12' }, 'not_your_unit');
  rejected({ ...state, cells: state.cells.map(cell => cell.id === 'H09' ? { ...cell, passable: false } : cell) },
    'MOVE', { unitId: 'sun-naga', to: 'H09' }, 'impassable');
  rejected(position([unit('n', 'sun', 'naga', 'H10', { exhausted: true })]), 'MOVE', { unitId: 'n', to: 'H09' }, 'exhausted');
  rejected({ ...state, ap: 0 }, 'MOVE', { unitId: 'sun-naga', to: 'H09' }, 'not_enough_ap');
});

test('closed action parsing rejects malformed, stale, unknown and out-of-turn commands', () => {
  const state = createInitialState();
  for (const input of [null, [], 'MOVE', {}, { type: 'END_TURN', player: 'sun', turn: NaN },
    action(state, 'MAGIC'), action(state, 'RECRUIT', { kind: 'asura', to: 'H02' }),
    action(state, 'END_TURN', { ap: 99 }), action(state, 'MOVE', { unitId: [], to: 'H02' })]) {
    assert.equal(applyAction(state, input).error.code, 'invalid_action');
  }
  assert.equal(applyAction(state, { type: 'END_TURN', player: 'moon', turn: 1 }).error.code, 'wrong_player');
  assert.equal(applyAction(state, { type: 'END_TURN', player: 'sun', turn: 3 }).error.code, 'stale_turn');
});

test('both players start with equal Soma; later turns reset only the incoming army and grant income', () => {
  let state = act(createInitialState(), 'MOVE', { unitId: 'sun-naga', to: 'H09' });
  state = act(state, 'END_TURN');
  assert.equal(state.activePlayer, 'moon'); assert.equal(state.round, 1); assert.equal(state.ap, 6);
  assert.deepEqual(state.soma, { sun: 4, moon: 4 });
  assert.equal(state.units.find(unit => unit.id === 'sun-naga').moved, true);
  state = act(state, 'END_TURN');
  assert.equal(state.activePlayer, 'sun'); assert.equal(state.round, 2); assert.equal(state.turn, 3);
  assert.deepEqual(state.soma, { sun: 6, moon: 4 });
  assert.equal(state.units.find(unit => unit.id === 'sun-naga').moved, false);
});

test('Deva cannot move then attack or attack then move; restriction resets next own turn', () => {
  let state = position([unit('d', 'sun', 'deva', 'H09'), unit('n', 'moon', 'naga', 'H11')]);
  state = act(state, 'MOVE', { unitId: 'd', to: 'H10' });
  rejected(state, 'ATTACK', { unitId: 'd', targetId: 'n' }, 'deva_move_attack');
  state = act(act(state, 'END_TURN'), 'END_TURN');
  state = act(state, 'ATTACK', { unitId: 'd', targetId: 'n' });
  rejected(state, 'MOVE', { unitId: 'd', to: 'H12' }, 'deva_move_attack');
  assert.equal(unitAt(state, 'H11').id, 'd');
});

test('combat: stronger attacker advances, weaker attacker dies and ties hold', () => {
  for (const [attackerKind, defenderKind, outcome] of [
    ['deva', 'naga', 'attacker_wins'], ['naga', 'deva', 'defender_wins'], ['naga', 'naga', 'standoff'],
  ]) {
    const state = freeze(position([unit('a', 'sun', attackerKind, 'H10'), unit('b', 'moon', defenderKind, 'H11')]));
    const result = applyAction(state, action(state, 'ATTACK', { unitId: 'a', targetId: 'b' }));
    assert.equal(result.combat.result, outcome); assert.equal(result.state.ap, 4);
    assert.deepEqual(result, applyAction(state, action(state, 'ATTACK', { unitId: 'a', targetId: 'b' })));
    assert.equal(result.state.units.length, outcome === 'standoff' ? 2 : 1);
    if (outcome === 'attacker_wins') assert.equal(unitAt(result.state, 'H11').id, 'a');
    if (outcome === 'defender_wins') assert.equal(unitAt(result.state, 'H10'), undefined);
    if (outcome === 'standoff') rejected(result.state, 'ATTACK', { unitId: 'a', targetId: 'b' }, 'already_attacked');
  }
});

test('attack rejects friendly, missing, far-away and unaffordable targets', () => {
  const state = createInitialState();
  rejected(state, 'ATTACK', { unitId: 'sun-naga', targetId: 'sun-deva' }, 'invalid_target');
  rejected(state, 'ATTACK', { unitId: 'sun-naga', targetId: 'missing' }, 'invalid_target');
  rejected(state, 'ATTACK', { unitId: 'sun-naga', targetId: 'moon-deva' }, 'not_adjacent');
  rejected({ ...state, ap: 1 }, 'ATTACK', { unitId: 'sun-naga', targetId: 'moon-deva' }, 'not_enough_ap');
});

test('Nagabanda none, partial, full; Naga influence and impassable terrain count', () => {
  const target = unit('r', 'sun', 'rishi', 'H10');
  const empty = position([target]);
  assert.equal(nagabanda(empty, target).level, 'none');
  assert.equal(nagabanda(position([target, unit('e', 'moon', 'deva', 'H11')]), target).level, 'partial');
  const ring = neighbors(empty, 'H10').map((cell, i) => unit(`e${i}`, 'moon', 'deva', cell.id));
  const surrounded = position([target, ...ring]);
  assert.equal(nagabanda(surrounded, target).level, 'full');
  assert.equal(rishiEscapeCell(surrounded, target), null);
  const nagaState = position([target, unit('n', 'moon', 'naga', 'H11')]);
  assert.deepEqual(nagabanda(nagaState, target).blocked, ['H06', 'H11', 'H15']);
  const blockedTerrain = { ...empty, cells: empty.cells.map(cell => cell.id !== 'H10' ? { ...cell, passable: false } : cell) };
  assert.equal(nagabanda(blockedTerrain, target).level, 'full');
});

test('own units are not Nagabanda blockers; map edges consider only real neighbors', () => {
  const target = unit('r', 'sun', 'rishi', 'H01');
  const state = position([target]);
  const friends = neighbors(state, 'H01').map((cell, i) => unit(`f${i}`, 'sun', 'naga', cell.id));
  assert.equal(nagabanda(position([target, ...friends]), target).level, 'none');
  assert.equal(rishiEscapeCell(position([target, ...friends]), target), null);
  const enemies = friends.map(unit => ({ ...unit, owner: 'moon', kind: 'deva' }));
  assert.equal(nagabanda(position([target, ...enemies]), target).level, 'full');
  assert.equal(nagabanda(state, target).level, 'none');
});

test('own Ashram on or adjacent to a surrounded unit breaks full Nagabanda; enemy Ashram does not', () => {
  const target = unit('r', 'sun', 'rishi', 'H10');
  const ring = neighbors(createInitialState(), 'H10').map((cell, i) => unit(`e${i}`, 'moon', 'deva', cell.id));
  for (const cellId of ['H10', 'H09']) {
    const state = position([target, ...ring], { ashrams: [{ id: 'home', owner: 'sun', cellId }] });
    assert.equal(nagabanda(state, target).level, 'partial');
    assert.equal(nagabanda(state, target).protectedByAshram, true);
    assert.equal(rishiEscapeCell(state, target), null); // shelter cannot create a free cell
    assert.equal(nagabanda({ ...state, ashrams: [{ id: 'enemy', owner: 'moon', cellId }] }, target).level, 'full');
  }
});

test('full Nagabanda reduces combat strength by one; Ashram adds one for its defender', () => {
  const target = unit('n', 'sun', 'naga', 'H10');
  const ring = neighbors(createInitialState(), 'H10').map((cell, i) => unit(`e${i}`, 'moon', 'naga', cell.id));
  const state = position([target, ...ring]);
  assert.equal(resolveCombat(state, ring[0], target).defenderStrength, 1);
  assert.equal(resolveCombat({ ...state, ashrams: [{ id: 'home', owner: 'sun', cellId: 'H10' }] }, ring[0], target).defenderStrength, 3);
});

test('Rishi escapes once deterministically and a second attack in the same turn resolves combat', () => {
  const rishi = unit('r', 'moon', 'rishi', 'H10');
  let state = position([unit('d', 'sun', 'deva', 'H09'), unit('n', 'sun', 'deva', 'H01'), rishi]);
  const first = applyAction(state, action(state, 'ATTACK', { unitId: 'd', targetId: 'r' }));
  assert.equal(first.combat.result, 'escaped'); assert.equal(first.combat.escapeTo, 'H05');
  state = first.state;
  assert.equal(unitAt(state, 'H10').id, 'd'); assert.equal(unitAt(state, 'H05').fled, true);
  const second = applyAction(state, action(state, 'ATTACK', { unitId: 'n', targetId: 'r' }));
  assert.equal(second.combat.result, 'attacker_wins');
  assert.equal(second.state.units.some(unit => unit.id === 'r'), false);
  const moonTurn = act(state, 'END_TURN');
  assert.equal(moonTurn.units.find(unit => unit.id === 'r').fled, false);
});

test('Rishi escape excludes impassable, occupied, enemy-Naga-influenced and enemy-Ashram cells', () => {
  const rishi = unit('r', 'moon', 'rishi', 'H01');
  const state = position([rishi, unit('n', 'sun', 'naga', 'H09')], {
    ashrams: [{ id: 'enemy', owner: 'sun', cellId: 'H02' }],
  });
  assert.equal(rishiEscapeCell(state, rishi), null); // H04/H05 in Naga influence, H02 enemy Ashram
});

test('recruitment spends AP/Soma, allocates repeatable IDs and waits for the next own turn', () => {
  const initial = freeze(createInitialState());
  let state = act(initial, 'RECRUIT', { kind: 'naga', to: 'H02' });
  assert.equal(state.ap, 4); assert.equal(state.soma.sun, 2);
  assert.equal(unitAt(state, 'H02').id, 'sun-7');
  rejected(state, 'MOVE', { unitId: 'sun-7', to: 'H03' }, 'exhausted');
  rejected(initial, 'RECRUIT', { kind: 'naga', to: 'H10' }, 'not_at_ashram');
  rejected(initial, 'RECRUIT', { kind: 'naga', to: 'H01' }, 'occupied');
  rejected({ ...initial, soma: { sun: 0, moon: 4 } }, 'RECRUIT', { kind: 'deva', to: 'H02' }, 'not_enough_soma');
  state = act(act(state, 'END_TURN'), 'END_TURN');
  assert.equal(unitAt(state, 'H02').exhausted, false);
  state = act(state, 'MOVE', { unitId: 'sun-7', to: 'H03' });
  assert.equal(unitAt(state, 'H03').id, 'sun-7');
});

test('recruitment onto a free own Ashram is allowed but never onto an enemy Ashram', () => {
  const initial = createInitialState();
  const state = { ...initial, units: [] };
  assert.equal(act(state, 'RECRUIT', { kind: 'rishi', to: 'H01' }).units.length, 1);
  const adjacentBases = { ...state, ashrams: [...state.ashrams, { id: 'other', owner: 'moon', cellId: 'H02' }] };
  rejected(adjacentBases, 'RECRUIT', { kind: 'naga', to: 'H02' }, 'not_at_ashram');
});

test('Nagabanda query is read-only and costs no AP', () => {
  const state = freeze(createInitialState());
  const result = applyAction(state, action(state, 'NAGABANDA_CHECK', { unitId: 'moon-naga' }));
  assert.equal(result.ok, true); assert.equal(result.state, state); assert.equal(result.nagabanda.level, 'none');
  rejected(state, 'NAGABANDA_CHECK', { unitId: 'missing' }, 'unknown_unit');
});

test('a complete match from the canonical start can be won and replayed byte-for-byte', () => {
  let session = createSession();
  function play(type, fields = {}) { session = recordAction(session, action(session.state, type, fields)).session; }
  play('MOVE', { unitId: 'sun-deva', to: 'H10' });
  play('MOVE', { unitId: 'sun-deva', to: 'H14' });
  play('MOVE', { unitId: 'sun-deva', to: 'H18' });
  play('END_TURN'); play('END_TURN');
  play('ATTACK', { unitId: 'sun-deva', targetId: 'moon-rishi' });
  assert.equal(session.state.winner, 'sun');
  play('END_TURN'); // rejected commands are preserved, never applied
  assert.equal(session.log.at(-1).reason, 'game_over');
  const replayed = importReplay(exportReplay(session));
  assert.deepEqual(replayed, session);
  assert.equal(JSON.stringify(replayed.state), JSON.stringify(session.state));
});

test('moving onto an empty opposing Ashram ends the match immediately', () => {
  const state = position([unit('n', 'sun', 'naga', 'H18')], { ashrams: [{ id: 'enemy', owner: 'moon', cellId: 'H19' }] });
  assert.equal(act(state, 'MOVE', { unitId: 'n', to: 'H19' }).winner, 'sun');
});

test('40 completed rounds produce a bounded draw, including pass-only play', () => {
  let state = createInitialState();
  for (let i = 0; i < RULES.maxRounds * 2; i++) state = act(state, 'END_TURN');
  assert.equal(state.round, 40); assert.equal(state.winner, 'draw');
  rejected(state, 'END_TURN', {}, 'game_over');
});

test('replay rejects tampering, unsupported versions, malformed input and oversized histories', () => {
  const session = recordAction(createSession(), { type: 'MOVE', player: 'sun', turn: 1, unitId: 'sun-naga', to: 'H09' }).session;
  const doc = JSON.parse(exportReplay(session));
  for (const change of [
    d => { d.version = 99; }, d => { d.ruleset = 'other'; }, d => { d.initialState = {}; },
    d => { d.log[0].sequence = 2; }, d => { d.log[0].accepted = false; },
    d => { d.log[0].reason = 'game_over'; }, d => { d.log[0].action.to = 'H19'; },
    d => { d.log[0].action.ap = 99; }, d => { d.log[0].action = null; },
    d => { d.log = Array(MAX_REPLAY_ACTIONS + 1).fill(d.log[0]); },
  ]) {
    const copy = structuredClone(doc); change(copy);
    assert.throws(() => importReplay(JSON.stringify(copy)));
  }
  for (const text of ['null', '[]', '{', '{}', 'x'.repeat(MAX_REPLAY_CHARACTERS + 1)]) assert.throws(() => importReplay(text));
  assert.throws(() => recordAction({ state: session.state, log: Array(MAX_REPLAY_ACTIONS).fill(session.log[0]) }, doc.log[0].action));
});

test('recorded actions are detached from mutable caller objects', () => {
  const input = { type: 'MOVE', player: 'sun', turn: 1, unitId: 'sun-naga', to: 'H09' };
  const { session } = recordAction(freeze(createSession()), input);
  input.to = 'H19';
  assert.equal(session.log[0].action.to, 'H09');
  assert.deepEqual(importReplay(exportReplay(session)), session);
});

test('deterministic headless games preserve occupancy, resources and replay across varied legal actions', () => {
  for (const seed of [3, 17, 71]) {
    let counter = seed;
    let session = createSession();
    for (let step = 0; step < 600 && !session.state.winner; step++) {
      const state = session.state;
      const candidates = [action(state, 'END_TURN')];
      for (const unit of state.units.filter(unit => unit.owner === state.activePlayer)) {
        for (const cell of neighbors(state, unit.cellId)) {
          const target = unitAt(state, cell.id);
          candidates.push(target ? action(state, 'ATTACK', { unitId: unit.id, targetId: target.id })
            : action(state, 'MOVE', { unitId: unit.id, to: cell.id }));
        }
      }
      for (const kind of ['naga', 'rishi', 'deva']) {
        for (const cell of state.cells) candidates.push(action(state, 'RECRUIT', { kind, to: cell.id }));
      }
      const legal = candidates.filter(candidate => validateAction(state, candidate).ok);
      counter = (Math.imul(counter, 1664525) + 1013904223) >>> 0;
      session = recordAction(session, legal[counter % legal.length]).session;
      assert.equal(new Set(session.state.units.map(unit => unit.cellId)).size, session.state.units.length);
      assert.equal(new Set(session.state.units.map(unit => unit.id)).size, session.state.units.length);
      assert.ok(session.state.ap >= 0 && session.state.ap <= 6);
      assert.ok(session.state.soma.sun >= 0 && session.state.soma.moon >= 0);
    }
    assert.ok(session.state.winner, `seed ${seed} must terminate within 600 actions`);
    assert.deepEqual(importReplay(exportReplay(session)), session);
  }
});
