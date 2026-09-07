import type { PersonaProfileScope } from '../organizations/persona-media/persona-profile.models';
import { avatarCandidate } from './meet-avatar-candidate';
import { effective, scope } from './meet-avatar-test-fixtures';
describe('image-only avatar candidates', () => {
  it('copies the exact Hub pin and image, without requiring voice or pretending to authorize output', () => {
    const value = effective(), result = avatarCandidate(value, scope);
    expect(result.mode).toBe('persona-image-v1');
    if (result.mode !== 'persona-image-v1') throw new Error();
    expect(result.profile).toEqual(value.selection); expect(result.profile).not.toBe(value.selection);
    expect(result.reference).toEqual(value.media[0].asset); expect(result.reference).not.toBe(value.media[0].asset);
  });
  it.each(['organization', 'kind', 'owner', 'project'] as const)('rejects foreign %s binding', key => {
    expect(() => avatarCandidate(effective(), { ...scope, [key]: 'foreign' } as PersonaProfileScope)).toThrow();
  });
  it.each(['missing', 'disabled', 'preview', 'kind', 'hash', 'duplicate', 'video', 'pin', 'runtime'])(
    'never selects a fallback for invalid %s metadata', change => {
      const value = effective(), image = value.media[0];
      if (change === 'missing') image.asset = null;
      if (change === 'disabled') image.state = 'disabled';
      if (change === 'preview') image.preview_allowed = false;
      if (change === 'kind') image.asset!.kind = 'video';
      if (change === 'hash') image.asset!.sha256 = 'bad';
      if (change === 'duplicate') value.media = [image, image, image, image];
      if (change === 'video') value.media[1].state = 'disabled';
      if (change === 'pin') value.selection.selection_digest = 'bad';
      if (change === 'runtime') value.runtime_bound = true as never;
      expect(() => avatarCandidate(value, scope)).toThrow();
    });
});
