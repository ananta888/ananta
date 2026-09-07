// Minimal deterministic metadata for avatar-candidate/picker unit tests only.
import type { PersonaEffectiveProfile, PersonaProfileScope } from '../organizations/persona-media/persona-profile.models';

export const scope: PersonaProfileScope = { hub: 'https://hub.test', project: 'p', organization: 'org', kind: 'organization', owner: 'org' };
export function effective(): PersonaEffectiveProfile {
  return { purpose: 'preview', runtime_bound: false, topology_revision: 1,
    selection: { organization_id: 'org', owner_kind: 'organization', owner_id: 'org', selection_digest: 'b'.repeat(64) },
    media: [
      { kind: 'image', state: 'asset', available: true, preview_allowed: true, publication_checked: false, origins: [],
        asset: { tenant_id: 't', project_id: 'p', artifact_id: 'img', revision: 1, sha256: 'a'.repeat(64), kind: 'image', classification: 'test_only' } },
      ...(['video', 'voice', 'style'] as const).map(kind => ({ kind, state: kind === 'voice' ? 'disabled' as const : 'missing' as const,
        available: false, preview_allowed: false, publication_checked: false as const, asset: null, origins: [] })),
    ] };
}
