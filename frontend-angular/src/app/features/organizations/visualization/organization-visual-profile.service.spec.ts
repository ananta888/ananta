import { describe, expect, it } from 'vitest';

import { OrganizationVisualProfileService } from './organization-visual-profile.service';

describe('OrganizationVisualProfileService', () => {
  it.each(['__proto__', 'prototype', 'constructor', 'CONSTRUCTOR'])(
    'rejects unsafe visual override key %s',
    key => {
      const service = new OrganizationVisualProfileService();

      service.setRoleOverride(key, {
        importance: 100,
        leadershipScope: 'organization',
        color: '#FF0000',
      });

      expect(Object.keys(service.profile().roleOverrides)).toEqual([]);
      expect(service.roleOverride(key)).toBeUndefined();
      expect(Object.getPrototypeOf({})).toBe(Object.prototype);
    },
  );

  it('rejects unknown enum values, malformed colors and non-finite numbers', () => {
    const service = new OrganizationVisualProfileService();
    const initial = service.profile();

    service.setNodeSizeMetric('chief_name' as never);
    service.setRoleOverride('slot-lead', { leadershipScope: 'chief' as never });
    service.setRoleOverride('slot-lead', { color: 'red' });
    service.setRoleOverride('slot-lead', { importance: Number.NaN });

    expect(service.profile().nodeSizeMetric).toBe(initial.nodeSizeMetric);
    expect(service.profile().roleOverrides).toEqual({});
  });
});
