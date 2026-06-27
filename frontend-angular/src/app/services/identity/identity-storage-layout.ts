/**
 * Single source of truth for all identity-related storage keys.
 * Every other module imports from here — no string literals scattered around.
 *
 * Naming scheme:
 *   ananta.<sphere>.<field>
 *
 * Encryption flag:
 *   - 'plaintext'  : stored as-is in localStorage
 *   - 'encrypted'  : stored encrypted via SecureTokenStorage (requires IndexedDB)
 */
export type StorageEncryption = 'plaintext' | 'encrypted';

export interface StorageKey {
  /** localStorage key name */
  key: string;
  /** Whether the value is encrypted at rest */
  encryption: StorageEncryption;
  /** What this key holds */
  purpose: string;
}

export const IDENTITY_STORAGE_LAYOUT = {
  hub: {
    accessToken: { key: 'ananta.user.token', encryption: 'plaintext', purpose: 'Hub access token (JWT)' } as StorageKey,
    refreshToken: { key: 'ananta.hub.refresh_token', encryption: 'encrypted', purpose: 'Hub refresh token' } as StorageKey,
  },
  oidc: {
    accessToken: { key: 'ananta.oidc.access_token', encryption: 'plaintext', purpose: 'OIDC access token (Keycloak)' } as StorageKey,
    refreshToken: { key: 'ananta.oidc.refresh_token', encryption: 'encrypted', purpose: 'OIDC refresh token' } as StorageKey,
  },
  legacy: {
    hubRefreshToken: { key: 'ananta.user.refresh_token', encryption: 'plaintext', purpose: 'Legacy Hub RT (pre-encryption), migrated on startup' } as StorageKey,
  },
} as const;

export type SphereName = keyof typeof IDENTITY_STORAGE_LAYOUT;

/**
 * Returns all storage keys across all spheres.
 * Used by storage-clear / logout-all operations.
 */
export function allIdentityKeys(): StorageKey[] {
  const out: StorageKey[] = [];
  for (const sphere of Object.keys(IDENTITY_STORAGE_LAYOUT)) {
    const fields = (IDENTITY_STORAGE_LAYOUT as Record<string, Record<string, StorageKey>>)[sphere];
    for (const f of Object.keys(fields)) {
      out.push(fields[f]);
    }
  }
  return out;
}

/**
 * Read a storage key by sphere + field. Returns null if absent.
 */
export function readIdentityKey(sphere: SphereName, field: string): string | null {
  const layout = IDENTITY_STORAGE_LAYOUT[sphere] as Record<string, StorageKey> | undefined;
  const entry = layout?.[field];
  if (!entry) return null;
  return localStorage.getItem(entry.key);
}

/**
 * Remove all known identity keys from localStorage.
 * Used by `IdentityRegistry.logoutAll()` and tests.
 */
export function clearAllIdentityStorage(): void {
  for (const k of allIdentityKeys()) {
    localStorage.removeItem(k.key);
  }
}