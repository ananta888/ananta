import { ModeCardPickerComponent } from './mode-card-picker.component';
import { WizardShellComponent } from './wizard-shell.component';

describe('shared form components', () => {
  it('keeps mode card options generic and selectable', () => {
    const cmp = new ModeCardPickerComponent();
    const selected: string[] = [];
    cmp.options = [
      { id: 'quick', title: 'Schnell', description: 'Kurzer Ablauf.' },
      { id: 'deep', title: 'Gruendlich', description: 'Mehr Nachweise.' },
    ];
    cmp.selectedId = 'quick';
    cmp.selectOption.subscribe(option => selected.push(option.id));

    cmp.selectOption.emit(cmp.options[1]);

    expect(cmp.options.length).toBe(2);
    expect(cmp.selectedId).toBe('quick');
    expect(selected).toEqual(['deep']);
  });

  it('exposes active wizard step and completion state', () => {
    const cmp = new WizardShellComponent();
    cmp.steps = [
      { id: 'goal', title: 'Ziel', helper: 'Beschreibe das Ziel.' },
      { id: 'review', title: 'Pruefen', helper: 'Kontrolliere die Angaben.' },
    ];
    cmp.activeIndex = 1;

    expect(cmp.activeStep()?.id).toBe('review');
    expect(cmp.isLastStep()).toBe(true);
  });
});
