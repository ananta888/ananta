import { Injectable } from '@angular/core';

import { canonicalSecurityJson } from './webrtc-secure-envelope';

export interface GroupMediaKeyContext {
  readonly tenantId: string;
  readonly roomId: string;
  readonly publicationId: string;
  readonly senderId: string;
  readonly mediaDomain: 'audio' | 'camera' | 'screenshare' | 'data';
  readonly keyEpoch: number;
}

/** Domain-separated HKDF adapter for sender/publication/epoch content keys. */
@Injectable({ providedIn: 'root' })
export class GroupMediaKeyDerivationService {
  async derive(rootSecret: Uint8Array, context: Readonly<GroupMediaKeyContext>): Promise<CryptoKey> {
    validateContext(context);
    if (rootSecret.byteLength !== 32) throw new Error('group_media_root_secret_invalid');
    const ownedSecret = new Uint8Array(rootSecret);
    try {
      const root = await crypto.subtle.importKey('raw', ownedSecret, 'HKDF', false, ['deriveKey']);
      const salt = await crypto.subtle.digest(
        'SHA-256',
        new TextEncoder().encode(`ananta.group-media-root.v1\0${context.tenantId}\0${context.roomId}`),
      );
      const info = new TextEncoder().encode(canonicalSecurityJson({
        domain: 'ananta.group-media-publication-key.v1',
        tenant_id: context.tenantId,
        room_id: context.roomId,
        publication_id: context.publicationId,
        sender_id: context.senderId,
        media_domain: context.mediaDomain,
        key_epoch: context.keyEpoch,
      }));
      return crypto.subtle.deriveKey(
        { name: 'HKDF', hash: 'SHA-256', salt, info },
        root,
        { name: 'AES-GCM', length: 256 },
        false,
        ['encrypt', 'decrypt'],
      );
    } finally {
      ownedSecret.fill(0);
    }
  }
}

function validateContext(value: GroupMediaKeyContext): void {
  const ids = [value.tenantId, value.roomId, value.publicationId, value.senderId];
  if (ids.some(item => !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(item))
      || !['audio', 'camera', 'screenshare', 'data'].includes(value.mediaDomain)
      || !Number.isSafeInteger(value.keyEpoch) || value.keyEpoch < 1) {
    throw new Error('group_media_key_context_invalid');
  }
}
