import { Injectable } from '@angular/core';

export interface KeyEnvelope {
  publicKeySpkiB64: string;
  fingerprint: string;
}

export interface EncryptedPayload {
  ivB64: string;
  ciphertextB64: string;
}

@Injectable({ providedIn: 'root' })
export class E2eEncryptionService {
  private static readonly KEY_STORE = 'ananta.e2e.ecdh.p256.v1';

  async ensureLocalKeyPair(): Promise<KeyEnvelope> {
    const existing = localStorage.getItem(E2eEncryptionService.KEY_STORE);
    if (existing) {
      const parsed = JSON.parse(existing) as { publicKeySpkiB64: string; privateKeyPkcs8B64: string; fingerprint: string };
      return { publicKeySpkiB64: parsed.publicKeySpkiB64, fingerprint: parsed.fingerprint };
    }

    const kp = await crypto.subtle.generateKey(
      { name: 'ECDH', namedCurve: 'P-256' },
      true,
      ['deriveKey', 'deriveBits'],
    );

    const spki = await crypto.subtle.exportKey('spki', kp.publicKey);
    const pkcs8 = await crypto.subtle.exportKey('pkcs8', kp.privateKey);
    const publicKeySpkiB64 = this.abToB64(spki);
    const privateKeyPkcs8B64 = this.abToB64(pkcs8);
    const fingerprint = await this.fingerprintSpki(publicKeySpkiB64);

    localStorage.setItem(E2eEncryptionService.KEY_STORE, JSON.stringify({
      publicKeySpkiB64,
      privateKeyPkcs8B64,
      fingerprint,
    }));

    return { publicKeySpkiB64, fingerprint };
  }

  async encryptForPeer(peerPublicSpkiB64: string, payload: unknown): Promise<EncryptedPayload> {
    const priv = await this.loadPrivateKey();
    const peerPub = await crypto.subtle.importKey(
      'spki',
      this.b64ToAb(peerPublicSpkiB64),
      { name: 'ECDH', namedCurve: 'P-256' },
      false,
      [],
    );

    const aesKey = await crypto.subtle.deriveKey(
      { name: 'ECDH', public: peerPub },
      priv,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    );

    const iv = crypto.getRandomValues(new Uint8Array(12));
    const plaintext = new TextEncoder().encode(JSON.stringify(payload));
    const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, aesKey, plaintext);

    return {
      ivB64: this.abToB64(iv.buffer),
      ciphertextB64: this.abToB64(ciphertext),
    };
  }

  async decryptFromPeer(peerPublicSpkiB64: string, encrypted: EncryptedPayload): Promise<unknown> {
    const priv = await this.loadPrivateKey();
    const peerPub = await crypto.subtle.importKey(
      'spki',
      this.b64ToAb(peerPublicSpkiB64),
      { name: 'ECDH', namedCurve: 'P-256' },
      false,
      [],
    );

    const aesKey = await crypto.subtle.deriveKey(
      { name: 'ECDH', public: peerPub },
      priv,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    );

    const plaintext = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: new Uint8Array(this.b64ToAb(encrypted.ivB64)) },
      aesKey,
      this.b64ToAb(encrypted.ciphertextB64),
    );

    return JSON.parse(new TextDecoder().decode(plaintext));
  }

  async fingerprintSpki(publicKeySpkiB64: string): Promise<string> {
    const digest = await crypto.subtle.digest('SHA-256', this.b64ToAb(publicKeySpkiB64));
    return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('');
  }

  private async loadPrivateKey(): Promise<CryptoKey> {
    const raw = localStorage.getItem(E2eEncryptionService.KEY_STORE);
    if (!raw) {
      await this.ensureLocalKeyPair();
    }
    const parsed = JSON.parse(localStorage.getItem(E2eEncryptionService.KEY_STORE) || '{}') as { privateKeyPkcs8B64?: string };
    if (!parsed.privateKeyPkcs8B64) throw new Error('missing_private_key');

    return crypto.subtle.importKey(
      'pkcs8',
      this.b64ToAb(parsed.privateKeyPkcs8B64),
      { name: 'ECDH', namedCurve: 'P-256' },
      false,
      ['deriveKey', 'deriveBits'],
    );
  }

  private abToB64(ab: ArrayBuffer): string {
    const bytes = new Uint8Array(ab);
    let binary = '';
    bytes.forEach((b) => { binary += String.fromCharCode(b); });
    return btoa(binary);
  }

  private b64ToAb(b64: string): ArrayBuffer {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
    return bytes.buffer;
  }
}
