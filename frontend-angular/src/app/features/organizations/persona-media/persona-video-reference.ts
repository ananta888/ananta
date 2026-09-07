import { PersonaAssetPage, PersonaVideoReference } from './persona-profile.models';
import { assetPage, assetReference } from './persona-asset-reference';

/** Validate the private response projection, not publication or Hub authority. */
export function videoReference(value: unknown, project: string, artifactId: string): PersonaVideoReference {
  return assetReference(value, project, artifactId, 'video');
}

export function videoPage(value: unknown, project: string): PersonaAssetPage<'video'> {
  return assetPage(value, project, 'video');
}
