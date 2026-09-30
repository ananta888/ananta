import { ChatSettingsMap } from '../services/chat-sessions.service';

/**
 * Predictive-guide ("PUG") presets of a chat session: the named setting bundles,
 * their descriptions and the pure detection of the preset a session matches.
 */

export const PUG_PRESETS = {
  quiet:    { predictive_guide_dwell_ms: 5000, predictive_guide_min_confidence: 0.7,  predictive_guide_multi_candidates: 1 },
  balanced: { predictive_guide_dwell_ms: 1500, predictive_guide_min_confidence: 0.55, predictive_guide_multi_candidates: 3 },
  eager:    { predictive_guide_dwell_ms: 800,  predictive_guide_min_confidence: 0.35, predictive_guide_multi_candidates: 5 },
} as const;

export type PugPresetName = keyof typeof PUG_PRESETS;

const PUG_DESCRIPTIONS: Record<string, string> = {
  quiet:    'dwell=5000ms, confidence=0.7, candidates=1 — Snake reagiert selten, nur bei klaren Änderungen',
  balanced: 'dwell=1500ms, confidence=0.55, candidates=3 — Ausgewogenes Verhalten',
  eager:    'dwell=800ms, confidence=0.35, candidates=5 — Snake reagiert häufig auf jede Änderung',
  custom:   'Individuelle Einstellungen aktiv',
};

export function resolvePugPreset(settings: ChatSettingsMap | undefined): PugPresetName | 'custom' {
  for (const [name, vals] of Object.entries(PUG_PRESETS) as Array<[string, Record<string, unknown>]>) {
    const matches = Object.entries(vals).every(([k, v]) => settings?.[k] === v || (!settings?.[k] && !v));
    if (matches) return name as PugPresetName;
  }
  const hasPugSettings = Object.keys(settings || {}).some(
    k => k.startsWith('predictive_guide_dwell') || k.startsWith('predictive_guide_min'),
  );
  if (!hasPugSettings) return 'balanced';
  return 'custom';
}

export function pugPresetDescription(preset: PugPresetName | 'custom'): string {
  return PUG_DESCRIPTIONS[preset] ?? '';
}

/** Setting key/value pairs to apply for a preset, in declaration order. */
export function pugPresetSettingEntries(preset: PugPresetName): Array<[string, number]> {
  return Object.entries(PUG_PRESETS[preset]);
}
