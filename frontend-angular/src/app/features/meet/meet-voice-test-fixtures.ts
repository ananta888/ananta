// Minimal deterministic metadata for voice candidate/picker/selection unit tests.
import type { PersonaEffectiveProfile } from '../organizations/persona-media/persona-profile.models';
import { effective } from './meet-avatar-test-fixtures';

export function effectiveVoice(): PersonaEffectiveProfile {
  const value = effective();
  return { ...value, media: value.media.map(row => row.kind === 'voice' ? {
    ...row, state: 'asset', available: true, preview_allowed: true,
    asset: { tenant_id: 't', project_id: 'p', artifact_id: 'voice', revision: 1, sha256: 'c'.repeat(64), kind: 'voice', classification: 'test_only' },
  } : { ...row, state: 'disabled', asset: null, available: false, preview_allowed: false }) };
}
