import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { SecurityStorageBannerComponent } from './security-storage-banner.component';
import { SecureTokenStorage } from '../services/secure-token-storage.service';

describe('SecurityStorageBannerComponent', () => {
  beforeEach(() => {
    globalThis.indexedDB = new IDBFactory() as unknown as IDBFactory;
  });

  it('renders nothing when SecureTokenStorage is available', async () => {
    TestBed.configureTestingModule({
      providers: [SecurityStorageBannerComponent, SecureTokenStorage],
    });
    const component = TestBed.inject(SecurityStorageBannerComponent);
    TestBed.inject(SecureTokenStorage)._clearCacheForTesting();
    await component.ngOnInit();
    expect(component.showBanner()).toBe(false);
  });

  it('shows banner when IndexedDB is unavailable', async () => {
    const original = globalThis.indexedDB;
    // Simulate IndexedDB unavailability: replace open() with one that throws.
    globalThis.indexedDB = {
      open: () => {
        throw new Error('IndexedDB blocked');
      },
    } as unknown as IDBFactory;
    TestBed.configureTestingModule({
      providers: [SecurityStorageBannerComponent, SecureTokenStorage],
    });
    const component = TestBed.inject(SecurityStorageBannerComponent);
    await component.ngOnInit();
    expect(component.showBanner()).toBe(true);
    expect(component.bannerMessage()).toContain('IndexedDB');
    globalThis.indexedDB = original;
  });

  it('renders the message when shown', async () => {
    const original = globalThis.indexedDB;
    globalThis.indexedDB = {
      open: () => {
        throw new Error('blocked');
      },
    } as unknown as IDBFactory;
    TestBed.configureTestingModule({
      providers: [SecurityStorageBannerComponent, SecureTokenStorage],
    });
    const component = TestBed.inject(SecurityStorageBannerComponent);
    await component.ngOnInit();
    expect(component.bannerMessage().length).toBeGreaterThan(10);
    globalThis.indexedDB = original;
  });
});
