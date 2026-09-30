import {
  VOICE_PROFILE_DELETION_EVENT,
  VOICE_PROFILE_DELETION_STORAGE_PREFIX,
} from './voice-long-run-spool';

/**
 * Notifies about voice profile deletions announced by other tabs (storage
 * tombstones) or by this document (custom event). Only non-empty profile ids
 * are reported; deciding whether a deletion is relevant stays with the owner.
 */
export class VoiceProfileDeletionWatcher {
  private readonly storageListener = (event: StorageEvent) => {
    if (!event.key?.startsWith(VOICE_PROFILE_DELETION_STORAGE_PREFIX)) return;
    const profileId = event.key.slice(VOICE_PROFILE_DELETION_STORAGE_PREFIX.length);
    if (profileId) this.deleted(profileId);
  };

  private readonly sameDocumentListener = (event: Event) => {
    const profileId = String((event as CustomEvent<{ profileId?: string }>).detail?.profileId || '');
    if (profileId) this.deleted(profileId);
  };

  constructor(private readonly deleted: (profileId: string) => void) {}

  attach(): void {
    globalThis.addEventListener?.('storage', this.storageListener);
    globalThis.addEventListener?.(VOICE_PROFILE_DELETION_EVENT, this.sameDocumentListener);
  }

  detach(): void {
    globalThis.removeEventListener?.('storage', this.storageListener);
    globalThis.removeEventListener?.(VOICE_PROFILE_DELETION_EVENT, this.sameDocumentListener);
  }
}
