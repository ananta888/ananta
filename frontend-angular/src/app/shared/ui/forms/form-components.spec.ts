import { ModeCardPickerComponent } from './mode-card-picker.component';
import { FormFieldComponent } from './form-field.component';
import { PresetPickerComponent } from './preset-picker.component';
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

  it('keeps wizard navigation outputs explicit', () => {
    const cmp = new WizardShellComponent();
    const events: string[] = [];
    cmp.steps = [
      { id: 'goal', title: 'Ziel', helper: 'Beschreibe das Ziel.' },
      { id: 'context', title: 'Kontext', helper: 'Grenzen und Eingaben.' },
    ];
    cmp.activeIndex = 0;
    cmp.next.subscribe(() => events.push('next'));
    cmp.previous.subscribe(() => events.push('previous'));
    cmp.submit.subscribe(() => events.push('submit'));
    cmp.stepSelect.subscribe(index => events.push(`step:${index}`));

    cmp.next.emit();
    cmp.previous.emit();
    cmp.submit.emit();
    cmp.stepSelect.emit(1);

    expect(events).toEqual(['next', 'previous', 'submit', 'step:1']);
  });

  it('keeps preset picker selections generic', () => {
    const cmp = new PresetPickerComponent();
    const selected: string[] = [];
    cmp.presets = [{ id: 'repo', title: 'Repository', description: 'Analyse' }];
    cmp.selectPreset.subscribe(preset => selected.push(preset.id));

    cmp.selectPreset.emit(cmp.presets[0]);

    expect(selected).toEqual(['repo']);
  });

  it('keeps form field metadata separate from projected controls', () => {
    const cmp = new FormFieldComponent();
    cmp.label = 'Kontext';
    cmp.hint = 'Hilft beim Planen.';
    cmp.required = true;

    expect(cmp.label).toBe('Kontext');
    expect(cmp.required).toBe(true);
  });
});
