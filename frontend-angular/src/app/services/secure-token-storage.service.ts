import { Injectable } from '@angular/core';

const DB_NAME = 'ananta-secure-tokens';
const STORE_NAME = 'keys';
const DB_VERSION = 1;

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

function base64ToArrayBuffer(b64: string): Uint8Array {
  const binary = atob(b64);
  // Return a Uint8Array (TypedArray = BufferSource) rather than its
  // underlying ArrayBuffer. In cross-realm contexts (e.g. when
  // SubtleCrypto from a node realm is asked to operate on an
  // ArrayBuffer allocated in the jsdom realm), node 20.x's stricter
  // SubtleCrypto.run-time checks reject the cross-realm ArrayBuffer
  // with "not instance of ArrayBuffer, Buffer, TypedArray, or
  // DataView". Passing the Uint8Array view works because TypedArray
  // matching is performed by ArrayBuffer.isView and does not require
  // realm identity on the underlying buffer.
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

@Injectable({ providedIn: 'root' })
export class SecureTokenStorage {
  private keyCache = new Map<string, CryptoKey>();
  private keyPromiseCache = new Map<string, Promise<CryptoKey>>();

  private openDb(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME);
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  private async getOrCreateKey(storageKey: string): Promise<CryptoKey> {
    if (this.keyCache.has(storageKey)) return this.keyCache.get(storageKey)!;
    if (this.keyPromiseCache.has(storageKey)) return this.keyPromiseCache.get(storageKey)!;

    const promise = (async () => {
      const db = await this.openDb();
      const existing: CryptoKey | undefined = await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readonly');
        const req = tx.objectStore(STORE_NAME).get(storageKey);
        req.onsuccess = () => resolve(req.result as CryptoKey | undefined);
        req.onerror = () => reject(req.error);
      });
      if (existing) {
        this.keyCache.set(storageKey, existing);
        return existing;
      }
      const key = await crypto.subtle.generateKey(
        { name: 'AES-GCM', length: 256 },
        false, // extractable: false — Key kann nicht exportiert werden
        ['encrypt', 'decrypt'],
      );
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);
        store.put(key, storageKey);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
      this.keyCache.set(storageKey, key);
      return key;
    })();
    this.keyPromiseCache.set(storageKey, promise);
    return promise;
  }

  async encrypt(plaintext: string, storageKey: string): Promise<string> {
    const key = await this.getOrCreateKey(storageKey);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv },
      key,
      new TextEncoder().encode(plaintext),
    );
    const ivB64 = arrayBufferToBase64(iv.buffer);
    const ctB64 = arrayBufferToBase64(ct);
    return `${ivB64}.${ctB64}`;
  }

  async decrypt(encrypted: string, storageKey: string): Promise<string> {
    const parts = encrypted.split('.');
    if (parts.length !== 2) throw new Error('Invalid encrypted format');
    const [ivB64, ctB64] = parts;
    // Cast through any: Uint8Array<ArrayBufferLike> is structurally a
    // BufferSource, but TS's lib.dom.d.ts pins the parameter to
    // ArrayBufferView<ArrayBuffer>, which excludes SharedArrayBuffer
    // backings. See base64ToArrayBuffer for the full explanation.
    const iv = base64ToArrayBuffer(ivB64) as any;
    const ct = base64ToArrayBuffer(ctB64) as any;
    const key = await this.getOrCreateKey(storageKey);
    const pt = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv },
      key,
      ct,
    );
    return new TextDecoder().decode(pt);
  }

  async isAvailable(): Promise<boolean> {
    try {
      await this.openDb();
      return true;
    } catch {
      return false;
    }
  }

  async getFallbackReason(): Promise<string | null> {
    if (await this.isAvailable()) return null;
    return 'IndexedDB nicht verfügbar — vermutlich Browser-Privacy-Mode oder InPrivate-Modus. Token-Encryption kann nicht aktiviert werden; Refresh-Tokens werden dann im Klartext in localStorage gespeichert.';
  }

  /** Clears the in-memory key cache. Test-helper only. */
  _clearCacheForTesting(): void {
    this.keyCache.clear();
    this.keyPromiseCache.clear();
  }
}
