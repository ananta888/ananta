import { Injectable } from '@angular/core';

const DEVICE_ID_STORAGE_KEY = 'ananta.pair-device-id.v1';
const DEVICE_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/;

/** Owns the stable, non-secret browser-device identifier used for Pair discovery. */
@Injectable({ providedIn: 'root' })
export class PairDeviceIdentityService {
  readonly id = loadOrCreateDeviceId();
}

function loadOrCreateDeviceId(): string {
  try {
    const existing = String(localStorage.getItem(DEVICE_ID_STORAGE_KEY) || '').trim();
    if (DEVICE_ID_RE.test(existing)) return existing;
  } catch { /* restricted storage falls back to one runtime-scoped id */ }

  const created = createDeviceId();
  try { localStorage.setItem(DEVICE_ID_STORAGE_KEY, created); } catch { /* runtime id remains stable */ }
  return created;
}

function createDeviceId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const random = new Uint8Array(16);
  crypto.getRandomValues(random);
  return `device-${Array.from(random, byte => byte.toString(16).padStart(2, '0')).join('')}`;
}
