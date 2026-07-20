import { Inject, Injectable, OnDestroy } from '@angular/core';
import { BehaviorSubject, Subject, filter, firstValueFrom, map, timeout } from 'rxjs';
import {
  SFU_ROOM_FACTORY,
  type SfuPublishedTrack,
  type SfuRemoteTrack,
  type SfuRoomFactory,
  type SfuRoomPort,
} from './livekit-sfu-room.adapter';

export interface SfuPublicationGrant {
  readonly publication_id: string;
  readonly source: 'microphone' | 'camera' | 'screen';
  readonly kind: 'audio' | 'video';
  readonly privacy: 'ordinary' | 'private_recovery';
  readonly authorized_subscriber_ids: readonly string[];
}

export interface SfuClientAdmission {
  readonly server_url: string;
  readonly access_token: string;
  readonly room_id: string;
  readonly membership_epoch: number;
  readonly expires_at: number;
  readonly publications: readonly SfuPublicationGrant[];
  readonly subscription_publication_ids: readonly string[];
  readonly strict_e2ee: boolean;
}

export interface SfuTransportView {
  readonly status: 'idle' | 'connecting' | 'connected' | 'fallback' | 'failed';
  readonly roomId: string | null;
  readonly membershipEpoch: number | null;
  readonly reasonCode: string;
  readonly publicationCount: number;
}

@Injectable({ providedIn: 'root' })
export class LivekitSfuTransportService implements OnDestroy {
  readonly state$ = new BehaviorSubject<SfuTransportView>(Object.freeze({
    status: 'idle', roomId: null, membershipEpoch: null, reasonCode: 'sfu_not_requested', publicationCount: 0,
  }));
  readonly remoteTrack$ = new Subject<SfuRemoteTrack>();
  readonly subscriberLost$ = new Subject<Readonly<{ receiverId: string }>>();
  private room: SfuRoomPort | null = null;
  private admission: SfuClientAdmission | null = null;
  private readonly published = new Map<string, SfuPublishedTrack>();
  private readonly confirmedSubscribersByPublication = new Map<string, Set<string>>();
  private readonly subscriberConfirmed$ = new Subject<Readonly<{ publicationId: string; receiverId: string }>>();
  private cleanup: (() => void)[] = [];

  constructor(@Inject(SFU_ROOM_FACTORY) private readonly rooms: SfuRoomFactory) {}

  async connect(
    admission: Readonly<SfuClientAdmission>,
    keyMaterial: Uint8Array,
    capability: 'supported' | 'unsupported' | 'unknown',
    nowSeconds = Math.trunc(Date.now() / 1000),
  ): Promise<'sfu' | 'ordinary'> {
    await this.disconnect('sfu_replaced');
    validateAdmission(admission, keyMaterial, nowSeconds);
    if (capability !== 'supported') {
      this.emit('fallback', admission, 'sfu_e2ee_capability_unknown'); return 'ordinary';
    }
    this.emit('connecting', admission, 'sfu_connecting');
    try {
      const room = await this.rooms.create(keyMaterial);
      if (admission.strict_e2ee && !room.e2eeSupported) throw new Error('sfu_e2ee_unsupported');
      this.room = room;
      room.denySubscriptionsByDefault();
      this.cleanup.push(room.onRemotePublication(publication => {
        const permitted = admission.subscription_publication_ids.includes(publication.publicationId);
        publication.setSubscribed(permitted);
      }));
      this.cleanup.push(room.onRemoteTrackSubscribed(value => {
        const permitted = admission.subscription_publication_ids.includes(value.publicationId);
        if (permitted) this.remoteTrack$.next(Object.freeze({ ...value }));
      }));
      this.cleanup.push(room.onLocalTrackSubscribed(publicationId => {
        const grant = admission.publications.find(value => value.publication_id === publicationId);
        // LiveKit reports that the first remote participant subscribed but
        // does not expose its identity here. Only a singleton, server-limited
        // audience can therefore be projected without guessing.
        if (!grant || grant.authorized_subscriber_ids.length !== 1) return;
        const receiverId = grant.authorized_subscriber_ids[0];
        this.confirmedSubscribersByPublication.set(publicationId, new Set([receiverId]));
        this.subscriberConfirmed$.next(Object.freeze({ publicationId, receiverId }));
        if (this.admission) this.emit('connected', this.admission, 'sfu_subscriber_confirmed');
      }));
      this.cleanup.push(room.onRemoteParticipantDisconnected(receiverId => {
        let removed = false;
        for (const [publicationId, confirmedIds] of this.confirmedSubscribersByPublication) {
          if (!confirmedIds.delete(receiverId)) continue;
          if (!confirmedIds.size) this.confirmedSubscribersByPublication.delete(publicationId);
          removed = true;
        }
        if (!removed) return;
        this.subscriberLost$.next(Object.freeze({ receiverId }));
        if (this.admission) this.emit('connected', this.admission, 'sfu_subscriber_disconnected');
      }));
      this.cleanup.push(room.onDisconnected(() => {
        const disconnectedAdmission = this.admission ?? admission;
        this.stopLocalTracks();
        this.emit('fallback', disconnectedAdmission, 'sfu_disconnected');
        void this.destroyRoom().finally(() => {
          if (this.admission === disconnectedAdmission) this.admission = null;
        });
      }));
      await room.connect(admission.server_url, admission.access_token);
      room.applyRemoteSubscriptions(new Set(admission.subscription_publication_ids));
      this.admission = admission;
      this.emit('connected', admission, 'sfu_connected');
      return 'sfu';
    } catch (error) {
      await this.destroyRoom();
      this.emit('fallback', admission, error instanceof Error ? error.message : 'sfu_connect_failed');
      return 'ordinary';
    }
  }

  async publish(publicationId: string, track: MediaStreamTrack): Promise<void> {
    const room = this.room; const admission = this.admission;
    if (!room || !admission || this.state$.value.status !== 'connected') throw new Error('sfu_not_connected');
    const grant = admission.publications.find(value => value.publication_id === publicationId);
    if (!grant || grant.privacy !== 'ordinary') throw new Error('sfu_publication_not_authorized');
    if (track.kind !== grant.kind) throw new Error('sfu_publication_kind_mismatch');
    if (this.published.has(publicationId)) throw new Error('sfu_publication_duplicate');
    const publication = await room.publish(publicationId, grant.source, track);
    this.published.set(publicationId, publication);
    this.applyAudiencePermissions();
    this.emit('connected', admission, 'sfu_connected');
    track.onended = () => { void this.unpublish(publicationId); };
  }

  async unpublish(publicationId: string): Promise<void> {
    const publication = this.published.get(publicationId);
    if (!publication) return;
    this.published.delete(publicationId);
    this.confirmedSubscribersByPublication.delete(publicationId);
    try { await this.room?.unpublish(publication); } finally {
      safeStop(publication.track); this.applyAudiencePermissions();
      if (this.admission) this.emit('connected', this.admission, 'sfu_connected');
    }
  }

  /** Read-only delivery projection used by receiver-path UI. */
  authorizedSubscriberIds(): ReadonlySet<string> {
    if (!this.admission || this.state$.value.status !== 'connected') return new Set<string>();
    return new Set([...this.confirmedSubscribersByPublication.values()].flatMap(value => [...value]));
  }

  /**
   * Project a receiver only after the authenticated Hub group-key ACK proves
   * that this admitted audience installed the current epoch and connected.
   */
  confirmAuthorizedSubscriber(publicationId: string, receiverId: string): void {
    const admission = this.admission;
    const grant = admission?.publications.find(value => value.publication_id === publicationId);
    if (!admission || this.state$.value.status !== 'connected' || !grant
        || !grant.authorized_subscriber_ids.includes(receiverId)) {
      throw new Error('sfu_subscriber_confirmation_not_authorized');
    }
    const confirmed = this.confirmedSubscribersByPublication.get(publicationId) ?? new Set<string>();
    confirmed.add(receiverId);
    this.confirmedSubscribersByPublication.set(publicationId, confirmed);
    this.subscriberConfirmed$.next(Object.freeze({ publicationId, receiverId }));
    this.emit('connected', admission, 'sfu_subscriber_confirmed');
  }

  async waitForSubscriber(publicationId: string, receiverId: string, timeoutMs = 15_000): Promise<boolean> {
    const admission = this.admission;
    const grant = admission?.publications.find(value => value.publication_id === publicationId);
    if (!admission || this.state$.value.status !== 'connected' || !grant
        || !grant.authorized_subscriber_ids.includes(receiverId)) return false;
    if (this.confirmedSubscribersByPublication.get(publicationId)?.has(receiverId)) return true;
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60_000) return false;
    try {
      return await firstValueFrom(this.subscriberConfirmed$.pipe(
        filter(value => value.publicationId === publicationId && value.receiverId === receiverId),
        map(() => true),
        timeout({ first: timeoutMs }),
      ));
    } catch { return false; }
  }

  async rotateKey(nextMembershipEpoch: number, keyMaterial: Uint8Array): Promise<void> {
    if (!this.room || !this.admission) throw new Error('sfu_not_connected');
    if (!Number.isSafeInteger(nextMembershipEpoch) || nextMembershipEpoch <= this.admission.membership_epoch
        || keyMaterial.byteLength !== 32) throw new Error('sfu_rekey_invalid');
    // Publishing is paused before the asynchronous key switch. A fresh Hub
    // admission is required before it can resume under the new epoch.
    for (const publication of this.published.values()) publication.track.enabled = false;
    await this.room.rotateKey(keyMaterial);
    await this.disconnect('sfu_rekey_admission_required');
  }

  async disconnect(reasonCode = 'sfu_user_disconnect'): Promise<void> {
    const previous = this.admission;
    this.stopLocalTracks();
    await this.destroyRoom();
    this.admission = null;
    this.confirmedSubscribersByPublication.clear();
    this.emit('idle', previous, reasonCode);
  }

  ngOnDestroy(): void {
    void this.disconnect('sfu_service_destroyed');
    this.subscriberConfirmed$.complete();
    this.remoteTrack$.complete();
    this.subscriberLost$.complete();
    this.state$.complete();
  }

  private applyAudiencePermissions(): void {
    if (!this.room || !this.admission) return;
    const permissions = new Map<string, readonly string[]>();
    for (const [publicationId, publication] of this.published) {
      const grant = this.admission.publications.find(value => value.publication_id === publicationId);
      if (grant) permissions.set(publication.trackSid, grant.authorized_subscriber_ids);
    }
    this.room.setTrackAudience(permissions);
  }

  private stopLocalTracks(): void {
    for (const publication of this.published.values()) safeStop(publication.track);
    this.published.clear();
    this.confirmedSubscribersByPublication.clear();
  }

  private async destroyRoom(): Promise<void> {
    for (const release of this.cleanup.splice(0)) release();
    const room = this.room; this.room = null;
    if (room) await room.disconnect().catch(() => undefined);
  }

  private emit(status: SfuTransportView['status'], admission: Readonly<SfuClientAdmission> | null, reasonCode: string) {
    this.state$.next(Object.freeze({
      status, roomId: admission?.room_id ?? null, membershipEpoch: admission?.membership_epoch ?? null,
      reasonCode, publicationCount: this.published.size,
    }));
  }
}

function validateAdmission(value: Readonly<SfuClientAdmission>, key: Uint8Array, nowSeconds: number): void {
  if (!value.server_url.startsWith('wss://') && !value.server_url.startsWith('ws://')) throw new Error('sfu_url_invalid');
  if (!value.access_token || !/^sfu-[a-f0-9]{32}$/.test(value.room_id)
      || !Number.isSafeInteger(value.membership_epoch) || value.membership_epoch < 1
      || !Number.isSafeInteger(value.expires_at) || value.expires_at <= nowSeconds || value.expires_at > nowSeconds + 60
      || key.byteLength !== 32) throw new Error('sfu_admission_invalid');
  const ids = new Set<string>();
  for (const grant of value.publications) {
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(grant.publication_id) || ids.has(grant.publication_id)) {
      throw new Error('sfu_publication_grant_invalid');
    }
    ids.add(grant.publication_id);
    if (grant.privacy === 'private_recovery') continue;
    if (grant.kind !== (grant.source === 'microphone' ? 'audio' : 'video')) throw new Error('sfu_publication_grant_invalid');
  }
}

function safeStop(track: MediaStreamTrack): void { try { track.stop(); } catch { /* deterministic cleanup */ } }
