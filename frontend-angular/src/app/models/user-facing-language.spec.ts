import {
  decisionExplanation,
  safetyBoundaryExplanation,
  userFacingTerm,
  userFacingTermLabel,
} from './user-facing-language';

describe('user-facing language helpers', () => {
  it('keeps technical terms available while exposing simpler labels', () => {
    expect(userFacingTermLabel('artifact')).toBe('Ergebnis');
    expect(userFacingTerm('verification').technicalLabel).toBe('Verification');
    expect(userFacingTerm('verification').hint).toContain('Qualitaetscheck');
  });

  it('explains platform decisions in user-facing language', () => {
    expect(decisionExplanation('routing')).toContain('Hub');
    expect(decisionExplanation('tool-approval')).toContain('nachvollziehbar');
  });

  it('turns safety states into actionable explanations', () => {
    expect(safetyBoundaryExplanation('blocked')).toContain('wartet bewusst');
    expect(safetyBoundaryExplanation('failed')).toContain('Logs');
  });
});
