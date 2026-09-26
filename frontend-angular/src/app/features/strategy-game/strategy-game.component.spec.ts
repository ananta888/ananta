import { ComponentFixture, TestBed } from '@angular/core/testing';
import { StrategyGameComponent } from './strategy-game.component';
import { createInitialState, unitAt } from '../../../../../packages/ananta-game-core/src';

describe('StrategyGameComponent', () => {
  let fixture: ComponentFixture<StrategyGameComponent>;
  let component: StrategyGameComponent;
  let element: HTMLElement;

  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [StrategyGameComponent] }).compileComponents();
    fixture = TestBed.createComponent(StrategyGameComponent);
    component = fixture.componentInstance;
    element = fixture.nativeElement;
    fixture.detectChanges();
  });

  function click(selector: string) {
    const button = element.querySelector<HTMLButtonElement>(selector);
    expect(button).not.toBeNull();
    button!.click();
    fixture.detectChanges();
  }
  const cell = (id: string) => click(`[data-cell="${id}"]`);
  const endTurn = () => click('[data-testid="end-turn"]');

  it('renders all 19 accessible hex buttons, armies, bases and resources', () => {
    expect(element.querySelectorAll('.hex')).toHaveLength(19);
    expect(element.querySelectorAll('.hex.selected, .hex.legal')).toHaveLength(0);
    expect(element.querySelector('[data-cell="H01"]')?.getAttribute('aria-label')).toContain('Ashram Sonne');
    expect(element.querySelector('[data-cell="H19"]')?.getAttribute('aria-label')).toContain('Mond: Rishi');
    expect(element.textContent).toContain('4 Soma · 3 Figuren');
  });

  it('selects a figure, highlights only valid actions, moves and spends AP through the core', () => {
    cell('H04');
    expect(element.querySelector('[data-cell="H09"]')?.classList.contains('legal')).toBe(true);
    expect(element.querySelector('[data-cell="H19"]')?.classList.contains('legal')).toBe(false);
    cell('H09');
    expect(unitAt(component.state, 'H09')?.id).toBe('sun-naga');
    expect(component.state.ap).toBe(5);
    expect(component.session.log).toHaveLength(1);
  });

  it('shows a bounded error for illegal moves without changing the board', () => {
    cell('H04');
    const state = component.state;
    cell('H17');
    expect(component.state).toBe(state);
    expect(component.session.log[0].accepted).toBe(false);
    expect(element.querySelector('[role="status"]')?.textContent).toContain('Nachbarfeld');
  });

  it('changes the active player, clears selection and prevents controlling the previous army', () => {
    cell('H04'); endTurn();
    expect(component.state.activePlayer).toBe('moon');
    expect(component.selectedUnitId).toBeNull();
    cell('H04'); cell('H09');
    expect(unitAt(component.state, 'H04')?.id).toBe('sun-naga');
    expect(component.session.log).toHaveLength(1);
  });

  it('recruits with real costs and prevents acting before the next own turn', () => {
    click('[data-recruit="naga"]'); cell('H02');
    expect(component.state.soma.sun).toBe(2);
    expect(component.state.ap).toBe(4);
    cell('H02'); cell('H03');
    expect(component.hasError).toBe(true);
    expect(unitAt(component.state, 'H02')?.exhausted).toBe(true);
    endTurn(); endTurn();
    cell('H02'); cell('H03');
    expect(unitAt(component.state, 'H03')?.id).toBe('sun-7');
  });

  it('completes a local match using the actual rendered controls', () => {
    cell('H05'); cell('H10'); cell('H14'); cell('H18');
    endTurn(); endTurn(); cell('H18');
    expect(element.querySelector('[data-cell="H19"]')?.getAttribute('title')).toContain('Angreifer gewinnt');
    cell('H19');
    expect(component.state.winner).toBe('sun');
    expect(element.querySelector('[data-testid="winner"]')?.textContent).toContain('Sonne gewinnt');
    expect(element.querySelector('[data-testid="end-turn"]')).toBeNull();
    expect([...element.querySelectorAll<HTMLButtonElement>('.hex')].every(button => button.disabled)).toBe(true);
  });

  it('exports and restores a match and keeps the current match on invalid replay input', () => {
    cell('H04'); cell('H09'); endTurn();
    const session = component.session;
    component.saveReplay();
    const replay = component.replayText;
    component.restart(); component.replayText = replay; component.loadReplay();
    expect(component.session).toEqual(session);
    component.replayText = '{"version":99}'; component.loadReplay();
    expect(component.session).toEqual(session);
    expect(component.hasError).toBe(true);
  });

  it('starts a clean new match without retaining selection or logs', () => {
    cell('H04'); cell('H09');
    click('[data-testid="new-game"]');
    expect(component.state).toEqual(createInitialState());
    expect(component.session.log).toEqual([]);
    expect(component.selectedUnitId).toBeNull();
    expect(component.recruitKind).toBeNull();
  });
});
