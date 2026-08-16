import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';

import { PolicyService } from './policy.service';
import { SourceControlV1ApiClient } from '../../../services/source-control-v1-api.client';
import { ChServiceError, DEFAULT_WRITE_MODE_TIMEOUT_MS } from '../models/codehug.models';

function mockSourceControl() {
  return {
    listContextPolicies: vi.fn(),
  };
}

describe('PolicyService', () => {
  let service: PolicyService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        PolicyService,
        { provide: SourceControlV1ApiClient, useValue: mockSourceControl() },
      ],
    });
    service = TestBed.inject(PolicyService);
  });

  it('starts in read-only mode', () => {
    expect(service.writeMode()).toBe('read-only');
    expect(service.writeModeActive()).toBe(false);
  });

  it('armWriteMode switches to write-armed and sets expiry', () => {
    service.armWriteMode(60_000);
    expect(service.writeMode()).toBe('write-armed');
    expect(service.writeModeExpiresAt()).not.toBeNull();
    expect(service.writeModeActive()).toBe(true);
  });

  it('armWriteMode default timeout is 15 minutes', () => {
    service.armWriteMode();
    const exp = service.writeModeExpiresAt()!;
    const remaining = exp - Date.now();
    expect(remaining).toBeGreaterThan(DEFAULT_WRITE_MODE_TIMEOUT_MS - 5_000);
    expect(remaining).toBeLessThanOrEqual(DEFAULT_WRITE_MODE_TIMEOUT_MS);
  });

  it('disarmWriteMode returns to read-only', () => {
    service.armWriteMode(60_000);
    service.disarmWriteMode();
    expect(service.writeMode()).toBe('read-only');
    expect(service.writeModeExpiresAt()).toBeNull();
    expect(service.writeModeActive()).toBe(false);
  });

  it('ensureWriteModeValid disarms on expiry', async () => {
    service.armWriteMode(10);
    await new Promise(r => setTimeout(r, 50));
    const valid = service.ensureWriteModeValid();
    expect(valid).toBe(false);
    expect(service.writeMode()).toBe('read-only');
  });

  it('update: throws when write-mode is not active', () => {
    expect(() => service.update({ allowedPaths: ['/tmp'] })).toThrow(ChServiceError);
  });

  it('update: throws even when write-mode is active because legacy mutation is disabled', () => {
    service.armWriteMode(60_000);
    expect(() => service.update({ allowedPaths: ['/tmp'] })).toThrow(ChServiceError);
  });

  it('setWriteModeTimeout with 0 resets to default', () => {
    service.setWriteModeTimeout(0);
    service.armWriteMode();
    const exp = service.writeModeExpiresAt()!;
    const remaining = exp - Date.now();
    expect(remaining).toBeLessThanOrEqual(DEFAULT_WRITE_MODE_TIMEOUT_MS);
  });
});
