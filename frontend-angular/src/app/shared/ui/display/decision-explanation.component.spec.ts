import { DecisionExplanationComponent } from './decision-explanation.component';

describe('DecisionExplanationComponent', () => {
  it('uses user-facing term labels for default titles', () => {
    const cmp = new DecisionExplanationComponent();
    cmp.kind = 'routing';

    expect(cmp.titleText()).toBe('Warum Zuweisung?');
    expect(cmp.messageText()).toContain('weist Arbeit gezielt zu');
  });

  it('allows feature screens to override title and message', () => {
    const cmp = new DecisionExplanationComponent();
    cmp.kind = 'verification';
    cmp.title = 'Warum wird geprueft?';
    cmp.message = 'Eigene Erklaerung.';

    expect(cmp.titleText()).toBe('Warum wird geprueft?');
    expect(cmp.messageText()).toBe('Eigene Erklaerung.');
  });
});
