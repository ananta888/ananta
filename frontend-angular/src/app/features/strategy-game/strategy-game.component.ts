import { Component } from '@angular/core';
import {
  ActionLogEntry, GameAction, PlayerId, UnitKind, UNIT_RULES, RULES,
  createSession, recordAction, exportReplay, importReplay, unitAt, nagabanda, validateAction, resolveCombat, MAX_REPLAY_CHARACTERS,
} from '../../../../../packages/ananta-game-core/src';

const PLAYER_NAMES: Record<PlayerId, string> = { sun: 'Sonne', moon: 'Mond' };
const UNIT_NAMES: Record<UnitKind, string> = { naga: 'Naga', rishi: 'Rishi', deva: 'Deva' };
const LOKA_NAMES = { upper: 'Obere Loka', middle: 'Mittlere Loka', lower: 'Untere Loka' };

@Component({
  selector: 'app-strategy-game',
  standalone: true,
  template: `
    <section class="game" aria-labelledby="game-title">
      <header class="game-heading">
        <div><p class="eyebrow">Zwei Spieler · ein Brett</p><h1 id="game-title">Ananta</h1>
          <p>Umschließe deine Gegner. Beschütze deinen Ashram.</p></div>
        <button type="button" class="secondary" data-testid="new-game" (click)="restart()">Neue Partie</button>
      </header>

      <div class="scoreboard">
        @for (player of players; track player) {
          <div class="player" [class.active]="state.activePlayer === player && !state.winner" [class.moon]="player === 'moon'">
            <span class="player-symbol" aria-hidden="true">{{ player === 'sun' ? '☀' : '☾' }}</span>
            <div><strong>{{ playerName(player) }}</strong><span>{{ state.soma[player] }} Soma · {{ armySize(player) }} Figuren</span></div>
          </div>
        }
      </div>

      <div class="play-layout">
        <div class="board-panel">
          <div class="board-heading"><span>19 Felder · 3 Lokas</span><span>Runde {{ state.round }} / {{ rules.maxRounds }}</span></div>
          <div class="hex-board" role="group" aria-label="Spielfeld mit 19 Hexfeldern">
            @for (cell of cells; track cell.id) {
              <button type="button" class="hex" [class]="'hex ' + cell.loka"
                [class.sun-unit]="cell.unit?.owner === 'sun'" [class.moon-unit]="cell.unit?.owner === 'moon'"
                [class.selected]="!!cell.unit && cell.unit.id === selectedUnitId" [class.legal]="cell.legal"
                [class.encircled]="cell.encircled" [class.home]="!!cell.ashram" [class.sleeping]="cell.unit?.exhausted"
                [style.left.%]="cell.x" [style.top.%]="cell.y"
                [attr.data-cell]="cell.id" [attr.aria-label]="cell.label" [title]="cell.label"
                [attr.aria-pressed]="!!cell.unit && cell.unit.id === selectedUnitId" [disabled]="!!state.winner"
                (click)="chooseCell(cell.id)">
                <span class="cell-id">{{ cell.id }}</span>
                <strong class="piece">{{ cell.unit ? unitName(cell.unit.kind) : cell.id === 'H10' ? 'Meru' : '·' }}</strong>
                <span class="cell-detail">{{ cell.unit ? playerName(cell.unit.owner) : '' }}{{ cell.ashram ? ' ⌂' : '' }}</span>
              </button>
            }
          </div>
          <p class="board-legend">⌂ Ashram · grüner Rand: erlaubtes Ziel · gestrichelter Rand: volle Nagabanda</p>
        </div>

        <section class="controls" aria-label="Spielzug">
          @if (state.winner) {
            <p class="eyebrow">Partie beendet</p>
            <h2 data-testid="winner">{{ state.winner === 'draw' ? 'Unentschieden' : playerName(state.winner) + ' gewinnt' }}</h2>
            <p>{{ state.winner === 'draw' ? '40 Runden sind gespielt.' : 'Der gegnerische Ashram wurde besetzt.' }}</p>
          } @else {
            <p class="eyebrow">{{ playerName(state.activePlayer) }} am Zug</p>
            <h2>{{ state.ap }} <small>/ 6 AP</small></h2>
            <div class="ap-meter" aria-hidden="true">@for (point of apPoints; track point) { <span [class.spent]="point > state.ap"></span> }</div>
            <p class="instruction">{{ recruitKind ? unitName(recruitKind) + ': Wähle ein freies Feld am eigenen Ashram.' : 'Eigene Figur wählen, dann ein Nachbarfeld zum Bewegen oder einen Gegner zum Angreifen.' }}</p>
            @if (selectedUnit; as unit) {
              <div class="selection">
                <strong>{{ unitName(unit.kind) }} · {{ unit.cellId }}</strong>
                <p>Stärke {{ unitRules[unit.kind].strength }} · {{ encirclementLabel(unit.id) }}</p>
                @if (unit.exhausted) { <p>Ab dem nächsten eigenen Zug einsatzbereit.</p> }
                @if (unit.kind === 'deva') { <p>Bewegen oder angreifen pro Zug.</p> }
                @if (unit.kind === 'rishi') { <p>Eine automatische Flucht zwischen eigenen Zuganfängen.</p> }
              </div>
            }
            <h3>Verstärkung</h3>
            <div class="recruits">
              @for (kind of kinds; track kind) {
                <button type="button" class="secondary" [attr.data-recruit]="kind" [attr.aria-pressed]="recruitKind === kind"
                  [disabled]="state.ap < rules.recruitAp || state.soma[state.activePlayer] < unitRules[kind].somaCost"
                  (click)="selectRecruit(kind)">
                  {{ unitName(kind) }}<small>{{ unitRules[kind].somaCost }} Soma · 2 AP</small>
                </button>
              }
            </div>
            @if (recruitKind || selectedUnitId) { <button type="button" class="text-button" (click)="clearSelection()">Auswahl aufheben</button> }
            <button type="button" class="end-turn" data-testid="end-turn" (click)="endTurn()">Zug beenden →</button>
          }
          <p class="feedback" [class.error]="hasError" role="status" aria-live="polite">{{ message }}</p>
        </section>
      </div>

      <div class="below-board">
        <details open>
          <summary>So wird gespielt</summary>
          <p>Gewinne, indem eine deiner Figuren den gegnerischen Ashram besetzt. Sonne beginnt.
            Beide starten mit 4 Soma; ab dem zweiten eigenen Zug gibt es 2 Soma dazu.</p>
          <p>Bewegen kostet 1 AP, Angreifen 2 AP. Jede Figur greift höchstens einmal pro Zug an.
            Die stärkere Figur gewinnt und besetzt das Feld; bei Gleichstand bleiben beide stehen.
            Neue Figuren werden im nächsten eigenen Zug aktiv.</p>
          <p>Naga: Stärke 2 und Einfluss auf Nachbarfelder. Rishi: Stärke 1 und automatische Flucht
            auf das freie, unbeeinflusste Nachbarfeld mit der kleinsten Feldnummer. Deva: Stärke 3,
            darf pro Zug bewegen oder angreifen.</p>
          <p>Nagabanda: Sind alle vorhandenen Nachbarfelder durch Gegner, gegnerischen Naga-Einfluss
            oder Terrain blockiert, verliert die Figur 1 Stärke und Rishi kann nicht fliehen.
            Der eigene Ashram auf dem Feld oder daneben verhindert volle Einkreisung;
            ein Verteidiger auf seinem Ashram erhält +1 Stärke.</p>
          <p class="prototype-note">Lokaler Prototyp mit vorläufigen Balancewerten. Nach 40 Runden gilt eine Partie als unentschieden.
            Zum Aufbewahren vor dem Neuladen das Zugprotokoll exportieren.</p>
        </details>
        <details>
          <summary>Zugprotokoll ({{ session.log.length }})</summary>
          <ol class="history">
            @for (entry of recentLog; track entry.sequence) {
              <li [value]="entry.sequence" [class.error]="!entry.accepted">{{ describeEntry(entry) }}</li>
            }
          </ol>
          @if (!session.log.length) { <p>Noch keine Züge gespielt.</p> }
          <p>Das Protokoll rekonstruiert die Partie ab der Startstellung. Angezeigt werden die letzten 12 Aktionen.</p>
          <div class="replay-actions">
            <button type="button" class="secondary" (click)="saveReplay()">Protokoll exportieren</button>
            <button type="button" class="secondary" [disabled]="!replayText.trim()" (click)="loadReplay()">Partie aus Protokoll laden</button>
          </div>
          <label for="game-replay">Partie als JSON zum Kopieren oder Einfügen</label>
          <textarea id="game-replay" rows="6" [attr.maxlength]="maxReplayCharacters" spellcheck="false" [value]="replayText"
            (input)="replayText = $any($event.target).value"></textarea>
        </details>
      </div>
    </section>
  `,
  styles: [`
    :host { display: block; }
    .game { max-width: 1080px; margin: auto; color: var(--fg, #1e293b); }
    .game-heading { display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: 1.5rem; }
    h1 { font: 600 clamp(2.4rem, 6vw, 4rem)/1.1 Georgia, serif; margin: .2rem 0; letter-spacing: -.04em; }
    h2 { font-size: 2rem; margin: .5rem 0; } h2 small { font-size: 1rem; color: var(--muted, #64748b); }
    h3 { font-size: .9rem; margin-top: 1.5rem; } p { line-height: 1.55; }
    .eyebrow { text-transform: uppercase; letter-spacing: .13em; font-size: .72rem; color: var(--muted, #64748b); margin: 0; }
    button { font: inherit; cursor: pointer; border-radius: 8px; padding: .7rem .9rem; border: 1px solid var(--border, #cbd5e1); }
    button:disabled { cursor: default; opacity: .55; }
    button:focus-visible, textarea:focus-visible, summary:focus-visible { outline: 3px solid #0e7490; outline-offset: 3px; }
    .secondary { background: var(--surface-raised, #fff); color: var(--fg, #1e293b); }
    .scoreboard { display: grid; grid-template-columns: 1fr 1fr; gap: .75rem; margin-bottom: 1rem; }
    .player { border: 1px solid var(--border, #cbd5e1); border-radius: 12px; padding: .8rem 1rem; display: flex; align-items: center; gap: .8rem; }
    .player.active { border-color: #b7791f; box-shadow: inset 0 -3px #b7791f; }
    .player.moon.active { border-color: #6555a6; box-shadow: inset 0 -3px #6555a6; }
    .player-symbol { font-size: 2rem; color: #ad6c0b; } .moon .player-symbol { color: #8271c4; }
    .player div { display: grid; gap: .2rem; } .player div span { font-size: .8rem; color: var(--muted, #64748b); }
    .play-layout { display: grid; grid-template-columns: minmax(0, 1fr) 275px; gap: 1rem; align-items: start; }
    .board-panel { background: #132c2b; border-radius: 16px; padding: 1rem; color: #d5e6da; }
    .board-heading { display: flex; justify-content: space-between; gap: .5rem; font-size: .75rem; color: #bfd1c7; }
    .hex-board { position: relative; width: 100%; aspect-ratio: 520 / 440; margin: .5rem 0; }
    .hex { position: absolute; transform: translate(-50%, -50%); width: 16%; height: 21%; padding: 0;
      clip-path: polygon(50% 0, 100% 25%, 100% 75%, 50% 100%, 0 75%, 0 25%); border: 0; border-radius: 0;
      background: #d8e3cb; color: #203b32; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1px; }
    .hex.upper { background: #d8e3cb; } .hex.middle { background: #c2d6c6; } .hex.lower { background: #a7c3b8; }
    .hex::after { content: ''; position: absolute; inset: 9% 11%; border: 2px solid transparent; border-radius: 30%; pointer-events: none; }
    .hex.legal::after { border-color: #1c5c3e; background: #ffffff30; }
    .hex.encircled::after { border: 2px dashed #b91c1c; }
    .hex.selected::after, .hex:focus-visible::after { border: 3px solid #134e4a; }
    .hex.selected, .hex:focus-visible { background: #fcf8d5; outline: none; }
    .hex.sun-unit { color: #6b3504; } .hex.moon-unit { color: #352164; }
    .hex.sleeping .piece { opacity: .55; } .hex.home .cell-detail { font-weight: 700; }
    .hex:disabled { opacity: 1; } .hex .cell-id { font-size: clamp(.55rem, 1.2vw, .7rem); opacity: .8; }
    .piece { font-family: Georgia, serif; font-size: clamp(.75rem, 1.65vw, 1.15rem); }
    .cell-detail { font-size: clamp(.5rem, 1.1vw, .7rem); min-height: 1em; }
    .board-legend { font-size: .7rem; margin: 0; color: #bfd1c7; }
    .controls { padding: 1rem; background: var(--card-bg, #f8fafc); border: 1px solid var(--border, #cbd5e1); border-radius: 12px; }
    .ap-meter { display: flex; gap: 5px; margin-bottom: 1rem; } .ap-meter span { height: 6px; flex: 1; border-radius: 4px; background: #3c8275; }
    .ap-meter .spent { background: var(--border, #cbd5e1); }
    .instruction, .selection p, .feedback { font-size: .84rem; }
    .selection { border-left: 3px solid #3c8275; padding-left: .75rem; } .selection p { margin: .35rem 0; }
    .recruits { display: grid; gap: .4rem; } .recruits button { display: flex; justify-content: space-between; gap: .4rem; align-items: center; }
    .recruits button[aria-pressed=true] { border-color: #3c8275; box-shadow: inset 3px 0 #3c8275; }
    .recruits small { font-size: .72rem; } .end-turn { width: 100%; margin-top: 1rem; color: #fff; background: #245b50; border-color: #245b50; }
    .text-button { background: transparent; border: 0; font-size: .8rem; padding-left: 0; color: var(--accent, #0369a1); }
    .feedback { margin-bottom: 0; } .error { color: var(--tone-error-text, #b91c1c); }
    .below-board { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1rem; align-items: start; }
    details { padding: 1rem; border: 1px solid var(--border, #cbd5e1); border-radius: 12px; font-size: .85rem; }
    summary { font-weight: 600; cursor: pointer; } .prototype-note { color: var(--muted, #64748b); }
    .history { padding-left: 2rem; line-height: 1.8; } .replay-actions { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: 1rem; }
    textarea { width: 100%; box-sizing: border-box; margin-top: .5rem; background: var(--input-bg, #fff); color: inherit; border: 1px solid var(--border, #cbd5e1); border-radius: 6px; }
    @media (max-width: 740px) { .play-layout, .below-board { grid-template-columns: 1fr; } .game-heading { align-items: start; }
      .piece { font-size: clamp(.75rem, 3vw, 1.2rem); } .cell-detail, .hex .cell-id { font-size: clamp(.5rem, 2vw, .75rem); } }
  `],
})
export class StrategyGameComponent {
  readonly players: PlayerId[] = ['sun', 'moon'];
  readonly kinds: UnitKind[] = ['naga', 'rishi', 'deva'];
  readonly apPoints = [1, 2, 3, 4, 5, 6];
  readonly rules = RULES;
  readonly unitRules = UNIT_RULES;
  readonly maxReplayCharacters = MAX_REPLAY_CHARACTERS;
  session = createSession();
  selectedUnitId: string | null = null;
  recruitKind: UnitKind | null = null;
  replayText = '';
  message = 'Sonne beginnt. Wähle eine deiner drei Figuren.';
  hasError = false;

  get state() { return this.session.state; }
  get selectedUnit() { return this.state.units.find(unit => unit.id === this.selectedUnitId); }
  get recentLog() { return this.session.log.slice(-12); }
  playerName(player: PlayerId) { return PLAYER_NAMES[player]; }
  unitName(kind: UnitKind) { return UNIT_NAMES[kind]; }
  armySize(player: PlayerId) { return this.state.units.filter(unit => unit.owner === player).length; }

  get cells() {
    return this.state.cells.map(cell => {
      const unit = unitAt(this.state, cell.id);
      const ashram = this.state.ashrams.find(home => home.cellId === cell.id);
      const candidate = this.actionForCell(cell.id);
      const legal = !!candidate && validateAction(this.state, candidate).ok;
      const label = [cell.id, LOKA_NAMES[cell.loka], unit ? `${this.playerName(unit.owner)}: ${this.unitName(unit.kind)}` : 'frei',
        ashram ? `Ashram ${this.playerName(ashram.owner)}` : '', unit ? this.encirclementLabel(unit.id) : '',
        legal ? this.preview(candidate) : ''].filter(Boolean).join(', ');
      return { ...cell, unit, ashram, legal, label,
        encircled: unit && nagabanda(this.state, unit).level === 'full',
        x: (260 + 86.6 * (cell.q + cell.r / 2)) / 5.2, y: (220 + 75 * cell.r) / 4.4 };
    });
  }

  encirclementLabel(unitId: string): string {
    const unit = this.state.units.find(candidate => candidate.id === unitId);
    if (!unit) return '';
    const status = nagabanda(this.state, unit);
    if (status.protectedByAshram) return 'Ashram-Schutz';
    return { none: 'nicht eingekreist', partial: 'teilweise eingekreist', full: 'volle Nagabanda' }[status.level];
  }

  chooseCell(cellId: string): void {
    if (this.state.winner) return;
    const unit = unitAt(this.state, cellId);
    if (!this.recruitKind && unit?.owner === this.state.activePlayer) {
      this.selectedUnitId = unit.id;
      this.message = `${this.unitName(unit.kind)} auf ${cellId} ausgewählt.`;
      this.hasError = false;
      return;
    }
    const candidate = this.actionForCell(cellId);
    if (candidate) this.execute(candidate);
    else this.message = 'Wähle zuerst eine eigene Figur oder eine Verstärkung.';
  }

  selectRecruit(kind: UnitKind): void {
    this.selectedUnitId = null;
    this.recruitKind = kind;
    this.message = 'Wähle ein markiertes Feld am eigenen Ashram.';
    this.hasError = false;
  }

  clearSelection(): void { this.selectedUnitId = null; this.recruitKind = null; }

  endTurn(): void {
    this.execute({ type: 'END_TURN', player: this.state.activePlayer, turn: this.state.turn });
  }

  restart(): void {
    this.session = createSession(); this.clearSelection(); this.replayText = '';
    this.message = 'Neue Partie. Sonne beginnt.'; this.hasError = false;
  }

  saveReplay(): void {
    this.replayText = exportReplay(this.session);
    this.message = 'Zugprotokoll exportiert. Kopiere den Text, um die Partie aufzubewahren.';
    this.hasError = false;
  }

  loadReplay(): void {
    try {
      const session = importReplay(this.replayText);
      this.session = session; this.clearSelection(); this.hasError = false;
      this.message = `Partie aus ${session.log.length} protokollierten Aktionen geladen.`;
    } catch (error) { this.showError(error); }
  }

  describeEntry(entry: ActionLogEntry): string {
    const action = entry.action;
    const description = action.type === 'MOVE' ? `${action.unitId} → ${action.to}`
      : action.type === 'ATTACK' ? `${action.unitId} greift ${action.targetId} an`
      : action.type === 'RECRUIT' ? `${this.unitName(action.kind)} nach ${action.to} rekrutiert`
      : action.type === 'END_TURN' ? 'Zug beendet' : 'Einkreisung geprüft';
    return `${this.playerName(action.player)}: ${description}${entry.accepted ? '' : ' (abgelehnt)'}`;
  }

  private actionForCell(cellId: string): GameAction | null {
    const context = { player: this.state.activePlayer, turn: this.state.turn };
    if (this.recruitKind) return { ...context, type: 'RECRUIT', kind: this.recruitKind, to: cellId };
    if (!this.selectedUnit) return null;
    const target = unitAt(this.state, cellId);
    return target ? { ...context, type: 'ATTACK', unitId: this.selectedUnit.id, targetId: target.id }
      : { ...context, type: 'MOVE', unitId: this.selectedUnit.id, to: cellId };
  }

  private preview(action: GameAction): string {
    if (action.type !== 'ATTACK') return action.type === 'MOVE' ? 'Bewegen: 1 AP' : 'Rekrutieren: 2 AP';
    const attacker = this.state.units.find(unit => unit.id === action.unitId)!;
    const defender = this.state.units.find(unit => unit.id === action.targetId)!;
    const outcome = resolveCombat(this.state, attacker, defender);
    const result = { escaped: `Rishi flieht nach ${outcome.escapeTo}`, attacker_wins: 'Angreifer gewinnt',
      defender_wins: 'Angreifer geht verloren', standoff: 'Gleichstand' }[outcome.result];
    return `${result} (${outcome.attackerStrength}:${outcome.defenderStrength}), 2 AP`;
  }

  private execute(action: GameAction): void {
    try {
      const { session, result } = recordAction(this.session, action);
      this.session = session;
      this.hasError = !result.ok;
      if (result.ok === false) { this.message = result.error.message; return; }
      this.message = result.combat ? { escaped: `Rishi flieht nach ${result.combat.escapeTo}.`,
        attacker_wins: 'Angriff erfolgreich. Das Feld wurde besetzt.', defender_wins: 'Der Angreifer wurde geschlagen.',
        standoff: 'Gleichstand. Beide Figuren bleiben stehen.' }[result.combat.result]
        : action.type === 'END_TURN' ? `${this.playerName(this.state.activePlayer)} ist am Zug.`
        : action.type === 'RECRUIT' ? 'Verstärkung steht bereit und wird im nächsten eigenen Zug aktiv.' : 'Figur bewegt.';
      if (action.type === 'END_TURN' || action.type === 'RECRUIT' || this.state.winner) this.clearSelection();
      if (this.state.winner) this.message = this.state.winner === 'draw' ? 'Die Partie endet unentschieden.'
        : `${this.playerName(this.state.winner)} hat den gegnerischen Ashram besetzt.`;
    } catch (error) { this.showError(error); }
  }

  private showError(error: unknown): void {
    this.hasError = true;
    this.message = error instanceof Error ? error.message : 'Die Aktion konnte nicht ausgeführt werden.';
  }
}
