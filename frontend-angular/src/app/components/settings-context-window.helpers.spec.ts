import {
  CONTEXT_WINDOW_PROFILES,
  contextWindowConfigError,
  contextWindowSummaryRows,
  normalizeContextWindowConfigValue,
  normalizeContextWindowDraftValue,
} from './settings-config.helpers';

describe('context window settings helpers', () => {
  it('offers the 12k/32k/64k/128k profiles', () => {
    expect(CONTEXT_WINDOW_PROFILES).toEqual({
      compact_12k: 12288,
      standard_32k: 32768,
      full_64k: 65536,
      extended_128k: 131072,
    });
  });

  it('normalizes the draft (environment, profiles, aliases, custom)', () => {
    expect(normalizeContextWindowDraftValue(undefined)).toEqual({ profile: '', tokens: null });
    expect(normalizeContextWindowDraftValue({})).toEqual({ profile: '', tokens: null });
    expect(normalizeContextWindowDraftValue({ profile: 'FULL_64K', tokens: 999 })).toEqual({ profile: 'full_64k', tokens: null });
    expect(normalizeContextWindowDraftValue({ profile: '128k' })).toEqual({ profile: 'extended_128k', tokens: null });
    expect(normalizeContextWindowDraftValue({ profile: 'custom', tokens: '49152' })).toEqual({ profile: 'custom', tokens: 49152 });
    expect(normalizeContextWindowDraftValue({ tokens: 49152 })).toEqual({ profile: 'custom', tokens: 49152 });
    expect(normalizeContextWindowDraftValue({ profile: 'bogus' })).toEqual({ profile: '', tokens: null });
  });

  it('builds the POST /config wire format', () => {
    expect(normalizeContextWindowConfigValue({ profile: '' })).toEqual({});
    expect(normalizeContextWindowConfigValue({ profile: 'standard_32k', tokens: 1 })).toEqual({ profile: 'standard_32k' });
    expect(normalizeContextWindowConfigValue({ profile: 'custom', tokens: 49152 })).toEqual({ profile: 'custom', tokens: 49152 });
  });

  it('rejects custom windows outside 2048..1048576 tokens', () => {
    expect(contextWindowConfigError({ profile: 'full_64k' })).toBe('');
    expect(contextWindowConfigError({ profile: 'custom', tokens: 49152 })).toBe('');
    expect(contextWindowConfigError({ profile: 'custom', tokens: null })).toContain('Custom-Kontextfenster');
    expect(contextWindowConfigError({ profile: 'custom', tokens: 1000 })).toContain('2048');
    expect(contextWindowConfigError({ profile: 'custom', tokens: 5_000_000 })).toContain('1048576');
  });

  it('summarizes configured, detected and effective window with the derived budgets', () => {
    const rows = contextWindowSummaryRows({
      configured: { profile: 'extended_128k', tokens: 131072, source: 'runtime_profile' },
      detected_limits: { provider: 65536 },
      effective: { tokens: 65536, profile: 'full_64k', limited_by: 'provider' },
      budgets: {
        output_reserve_tokens: 4096,
        safety_margin_tokens: 3276,
        fixed_overhead_tokens: 12000,
        available_tokens: 46164,
        budgets_tokens: { evidence: 29491, tool_results_total: 32768, recovery_context: 16384 },
      },
      bundle_budgets_tokens: { compact: 8192, standard: 24576, full: 32768 },
    });
    const byLabel = Object.fromEntries(rows.map((row) => [row.label, row.value]));
    expect(byLabel['Konfiguriertes Fenster']).toContain('extended_128k');
    expect(byLabel['Konfiguriertes Fenster']).toContain('Einstellungen (Profil)');
    expect(byLabel['Erkanntes Provider-/Modelllimit']).toContain('Provider');
    expect(byLabel['Effektives Fenster']).toContain('full_64k');
    expect(byLabel['Effektives Fenster']).toContain('begrenzt durch: Provider');
    expect(byLabel['Verfuegbar fuer Material']).toMatch(/46\.164 Tokens/);
    expect(byLabel['Kontext-Buendel compact / standard / full']).toMatch(/8\.192 Tokens \/ 24\.576 Tokens \/ 32\.768 Tokens/);
    expect(byLabel['Recovery-Kontext']).toMatch(/16\.384 Tokens/);
  });

  it('reports an unavailable summary instead of failing', () => {
    expect(contextWindowSummaryRows(undefined)).toEqual([{ label: 'Status', value: 'Zusammenfassung nicht verfuegbar' }]);
    expect(contextWindowSummaryRows({ error: 'unavailable' })[0].label).toBe('Status');
  });

  it('shows that no limit was reported when the provider gives none', () => {
    const rows = contextWindowSummaryRows({ configured: { profile: 'standard_32k', tokens: 32768, source: 'default' },
      detected_limits: {}, effective: { tokens: 32768, profile: 'standard_32k', limited_by: 'configured' }, budgets: {} });
    expect(rows.find((row) => row.label === 'Erkanntes Provider-/Modelllimit')?.value).toContain('keines gemeldet');
  });
});
