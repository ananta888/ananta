import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach } from 'vitest';

import { ProfileStateService } from './profile-state.service';

describe('ProfileStateService', () => {
  let service: ProfileStateService;

  beforeEach(() => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({});
    service = TestBed.inject(ProfileStateService);
  });

  it('starts with public-ananta as default profile', () => {
    expect(service.profile().profile_id).toBe('public-ananta');
    expect(service.bridgeActive).toBe(false);
  });

  it('setProfile updates the signal', () => {
    service.setProfile({
      profile_id: 'local',
      oidc: {
        issuer: 'https://issuer.test',
        client_id: 'c',
        audience: 'a',
        pkce_required: true,
        bridge_active: true,
      },
    });
    expect(service.profile().profile_id).toBe('local');
    expect(service.bridgeActive).toBe(true);
    expect(service.oidcIssuer).toBe('https://issuer.test');
    expect(service.oidcClientId).toBe('c');
  });

  it('bridgeActive is false when oidc block is missing', () => {
    service.setProfile({ profile_id: 'local' });
    expect(service.bridgeActive).toBe(false);
    expect(service.oidcIssuer).toBe('');
    expect(service.oidcClientId).toBe('');
  });

  it('bridgeActive is false when bridge_active flag is undefined', () => {
    service.setProfile({
      profile_id: 'public-ananta',
      oidc: { issuer: 'i', client_id: 'c', audience: 'a', pkce_required: true },
    });
    expect(service.bridgeActive).toBe(false);
  });
});