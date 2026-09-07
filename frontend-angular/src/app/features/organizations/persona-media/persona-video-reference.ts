import { PersonaAssetPage, PersonaVideoReference } from './persona-profile.models';

/** Validate the private response projection, not publication or Hub authority. */
export function videoReference(value: unknown, project: string, artifactId: string): PersonaVideoReference {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).sort().join('|') !== 'artifact_id|classification|kind|project_id|revision|sha256|tenant_id') {
    throw new Error('persona_video_reference_invalid');
  }
  const reference = value as Partial<PersonaVideoReference>;
  const identifier = (id: unknown): boolean => typeof id === 'string' && /^[A-Za-z0-9_.:-]{1,160}$/.test(id);
  if (![reference.tenant_id, reference.project_id, reference.artifact_id].every(identifier)
    || reference.project_id !== project || reference.artifact_id !== artifactId
    || reference.kind !== 'video' || reference.revision !== 1
    || typeof reference.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(reference.sha256)
    || !['production', 'synthetic', 'test_only'].includes(reference.classification ?? '')) {
    throw new Error('persona_video_reference_invalid');
  }
  return reference as PersonaVideoReference;
}

export function videoPage(value: unknown, project: string): PersonaAssetPage<'video'> {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).sort().join('|') !== 'items|next_cursor|purpose') {
    throw new Error('persona_video_page_invalid');
  }
  const page = value as Partial<PersonaAssetPage<'video'>>;
  if (page.purpose !== 'preview' || !Array.isArray(page.items) || page.items.length > 20
    || !(page.next_cursor === null || typeof page.next_cursor === 'string' && /^[A-Za-z0-9_-]{43}$/.test(page.next_cursor))) {
    throw new Error('persona_video_page_invalid');
  }
  const items = page.items.map(item => videoReference(item, project, item?.artifact_id));
  if (new Set(items.map(item => item.artifact_id)).size !== items.length
    || new Set(items.map(item => item.tenant_id)).size > 1) {
    throw new Error('persona_video_page_invalid');
  }
  return { items, next_cursor: page.next_cursor, purpose: 'preview' };
}
