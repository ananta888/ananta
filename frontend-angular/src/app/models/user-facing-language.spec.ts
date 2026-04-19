import {
  USER_FACING_TERMS,
  decisionExplanation,
  safetyBoundaryExplanation,
  userFacingTerm,
  userFacingTermHint,
  userFacingTermLabel,
} from './user-facing-language';

describe('user-facing language helpers', () => {
  it('keeps every platform term mapped to a user label and technical label', () => {
    for (const [term, entry] of Object.entries(USER_FACING_TERMS)) {
      expect(entry.term).toBe(term);
      expect(entry.label.trim().length).toBeGreaterThan(0);
      expect(entry.technicalLabel.trim().length).toBeGreaterThan(0);
      expect(entry.hint.trim().length).toBeGreaterThan(0);
    }
  });

  it('returns stable labels and hints for known terms', () => {
    expect(userFacingTerm('artifact').label).toBe('Ergebnis');
    expect(userFacingTermLabel('routing')).toBe('Zuweisung');
    expect(userFacingTermHint('blocked')).toContain('Freigabe');
  });

  it('explains key decisions without exposing unnecessary implementation jargon', () => {
    expect(decisionExplanation('routing')).toContain('weist Arbeit gezielt zu');
    expect(decisionExplanation('verification')).toContain('prueft Ergebnisse');
    expect(decisionExplanation('tool-approval')).toContain('Tool-Nutzung bleibt begrenzt');
    expect(decisionExplanation('blocked')).toContain('pausiert');
  });

  it('maps safety boundaries to useful next-step language', () => {
    expect(safetyBoundaryExplanation('blocked')).toContain('wartet bewusst');
    expect(safetyBoundaryExplanation('failed')).toContain('gestoppt');
    expect(safetyBoundaryExplanation('review_required')).toContain('manuelle Freigabe');
    expect(safetyBoundaryExplanation(null)).toContain('Sicherheits- und Pruefhinweise');
  });
});
