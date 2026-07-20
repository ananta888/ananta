import { Injectable, InjectionToken, inject } from '@angular/core';

export interface StoredDeviceKeyPair {
  id: 'device-ecdh-current';
  keyPair: CryptoKeyPair;
  publicKeySpkiB64: string;
  fingerprint: string;
  generation: number;
  createdAtMs: number;
}

export interface SecureKeyStorePort {
  loadCurrent(): Promise<StoredDeviceKeyPair | null>;
  replaceCurrent(record: StoredDeviceKeyPair): Promise<void>;
  clear(): Promise<void>;
  discardLegacyLocalStorageKey(): boolean;
}

export class SecureKeyStoreError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}

@Injectable({ providedIn: 'root' })
export class SecureKeyStoreService implements SecureKeyStorePort {
  static readonly LEGACY_LOCAL_STORAGE_KEY = 'ananta.e2e.ecdh.p256.v1';
  private static readonly DB_NAME = 'ananta-secure-keys';
  private static readonly STORE_NAME = 'device-keys';
  private static readonly CURRENT_ID = 'device-ecdh-current';

  async loadCurrent(): Promise<StoredDeviceKeyPair | null> {
    const db = await this.openDb();
    try {
      const value = await this.request<StoredDeviceKeyPair | undefined>(
        db.transaction(SecureKeyStoreService.STORE_NAME, 'readonly')
          .objectStore(SecureKeyStoreService.STORE_NAME)
          .get(SecureKeyStoreService.CURRENT_ID),
      );
      if (!value) return null;
      if (
        value.id !== SecureKeyStoreService.CURRENT_ID
        || value.keyPair.privateKey.extractable
        || value.keyPair.privateKey.type !== 'private'
        || value.keyPair.publicKey.type !== 'public'
      ) {
        throw new SecureKeyStoreError('stored_key_invalid');
      }
      return value;
    } finally {
      db.close();
    }
  }

  async replaceCurrent(record: StoredDeviceKeyPair): Promise<void> {
    if (record.keyPair.privateKey.extractable) {
      throw new SecureKeyStoreError('private_key_extractable');
    }
    const db = await this.openDb();
    try {
      const tx = db.transaction(SecureKeyStoreService.STORE_NAME, 'readwrite');
      tx.objectStore(SecureKeyStoreService.STORE_NAME).put(record);
      await this.transactionComplete(tx);
    } catch (error) {
      if (error instanceof SecureKeyStoreError) throw error;
      throw new SecureKeyStoreError('secure_key_store_write_failed');
    } finally {
      db.close();
    }
  }

  async clear(): Promise<void> {
    const db = await this.openDb();
    try {
      const tx = db.transaction(SecureKeyStoreService.STORE_NAME, 'readwrite');
      tx.objectStore(SecureKeyStoreService.STORE_NAME).clear();
      await this.transactionComplete(tx);
    } catch {
      throw new SecureKeyStoreError('secure_key_store_cleanup_failed');
    } finally {
      db.close();
    }
  }

  discardLegacyLocalStorageKey(): boolean {
    try {
      const exists = localStorage.getItem(SecureKeyStoreService.LEGACY_LOCAL_STORAGE_KEY) !== null;
      if (exists) localStorage.removeItem(SecureKeyStoreService.LEGACY_LOCAL_STORAGE_KEY);
      return exists;
    } catch {
      // An inaccessible legacy store is never treated as trusted input.
      return true;
    }
  }

  private openDb(): Promise<IDBDatabase> {
    if (typeof indexedDB === 'undefined') {
      return Promise.reject(new SecureKeyStoreError('secure_key_store_unavailable'));
    }
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(SecureKeyStoreService.DB_NAME, 1);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(SecureKeyStoreService.STORE_NAME)) {
          request.result.createObjectStore(SecureKeyStoreService.STORE_NAME, { keyPath: 'id' });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(new SecureKeyStoreError('secure_key_store_open_failed'));
      request.onblocked = () => reject(new SecureKeyStoreError('secure_key_store_blocked'));
    });
  }

  private request<T>(request: IDBRequest<T>): Promise<T> {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(new SecureKeyStoreError('secure_key_store_read_failed'));
    });
  }

  private transactionComplete(tx: IDBTransaction): Promise<void> {
    return new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(new SecureKeyStoreError('secure_key_store_write_failed'));
      tx.onabort = () => reject(new SecureKeyStoreError('secure_key_store_write_failed'));
    });
  }
}

export const SECURE_KEY_STORE = new InjectionToken<SecureKeyStorePort>('SECURE_KEY_STORE', {
  providedIn: 'root',
  factory: () => inject(SecureKeyStoreService),
});
