import { GameAction, RuleErrorCode, parseAction } from './actions';
import { GameState } from './model';
import { createInitialState } from './board';
import { ActionResult, applyAction } from './applyAction';

export interface ActionLogEntry {
  readonly sequence: number;
  readonly action: GameAction;
  readonly accepted: boolean;
  readonly reason: RuleErrorCode | null;
}
export interface GameSession { readonly state: GameState; readonly log: readonly ActionLogEntry[] }
export const MAX_REPLAY_ACTIONS = 2048;
export const MAX_REPLAY_CHARACTERS = 1_000_000;

export function createSession(): GameSession { return { state: createInitialState(), log: [] }; }

export function recordAction(session: GameSession, input: GameAction): { session: GameSession; result: ActionResult } {
  if (session.log.length >= MAX_REPLAY_ACTIONS) throw new Error('Das Zugprotokoll ist voll. Bitte eine neue Partie starten.');
  const action = parseAction(input);
  if (!action) throw new Error('Die Aktion hat ein ungültiges Format.');
  const result = applyAction(session.state, action);
  const entry: ActionLogEntry = { sequence: session.log.length + 1, action, accepted: result.ok, reason: result.ok === true ? null : result.error.code };
  return { session: { state: result.state, log: [...session.log, entry] }, result };
}

export function exportReplay(session: GameSession): string {
  return JSON.stringify({ format: 'ananta-game-replay', version: 1, ruleset: 'brutal-mvp-v1', log: session.log }, null, 2);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

// Import only a bounded action history against the canonical start position.
// A replay is local game data, never Hub evidence or an authorization token.
export function importReplay(text: string): GameSession {
  if (text.length > MAX_REPLAY_CHARACTERS) throw new Error('Das Replay ist zu groß.');
  let document: unknown;
  try { document = JSON.parse(text); } catch { throw new Error('Das Replay enthält kein gültiges JSON.'); }
  if (!isRecord(document) || document.format !== 'ananta-game-replay' || document.version !== 1
    || document.ruleset !== 'brutal-mvp-v1' || !Array.isArray(document.log)
    || document.log.length > MAX_REPLAY_ACTIONS
    || Object.keys(document).some(key => !['format', 'version', 'ruleset', 'log'].includes(key))) {
    throw new Error('Unbekanntes oder ungültiges Replay-Format.');
  }
  let session = createSession();
  for (const [index, entry] of document.log.entries()) {
    if (!isRecord(entry) || entry.sequence !== index + 1 || typeof entry.accepted !== 'boolean'
      || Object.keys(entry).some(key => !['sequence', 'action', 'accepted', 'reason'].includes(key))
      || !parseAction(entry.action)) throw new Error(`Ungültiger Protokolleintrag ${index + 1}.`);
    const next = recordAction(session, entry.action as GameAction);
    const expected = next.session.log[index]!;
    if (expected.accepted !== entry.accepted || expected.reason !== entry.reason) {
      throw new Error(`Replay widerspricht den Spielregeln bei Aktion ${index + 1}.`);
    }
    session = next.session;
  }
  return session;
}
