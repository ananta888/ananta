import { assetPage, assetReference } from './persona-asset-reference';

export const voiceReference = (value: unknown, project: string, artifactId: string) => assetReference(value, project, artifactId, 'voice');
export const voicePage = (value: unknown, project: string) => assetPage(value, project, 'voice');
