import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PairDeviceIdentityService } from './pair-device-identity.service';

describe('PairDeviceIdentityService', () => {
  beforeEach(() => {
    localStorage.clear();
    TestBed.resetTestingModule();
  });

  it('persists one opaque device id without deriving it from the OIDC account', () => {
    const first = TestBed.inject(PairDeviceIdentityService).id;
    TestBed.resetTestingModule();
    const restored = TestBed.inject(PairDeviceIdentityService).id;

    expect(first).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/);
    expect(restored).toBe(first);
    expect(first).not.toContain('oidc');
  });

  it('keeps one random runtime id when persistent storage is unavailable', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('denied');
    });
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('denied');
    });
    try {
      const service = TestBed.inject(PairDeviceIdentityService);
      expect(service.id).toBe(service.id);
      expect(service.id).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/);
    } finally {
      getItem.mockRestore();
      setItem.mockRestore();
    }
  });
});
