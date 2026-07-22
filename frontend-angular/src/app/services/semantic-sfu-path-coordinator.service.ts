import { Injectable, InjectionToken, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, firstValueFrom, Subscription } from 'rxjs';

import { E2eEncryptionService } from './e2e-encryption.service';
import { LivekitSfuTransportService, SfuClientAdmission } from './livekit-sfu-transport.service';
import {
  SemanticSfuAdmissionApiService,
  SemanticSfuState,
  SemanticSfuToken,
} from './semantic-sfu-admission-api.service';
import {
  SemanticSfuPairSignalingService,
  SemanticSfuPublicationHint,
} from './semantic-sfu-pair-signaling.service';
import {
  ReceivedSfuGroupEpoch,
  SemanticSfuGroupContext,
  SemanticSfuGroupKeyService,
} from './semantic-sfu-group-key.service';
import { WebrtcMediaSessionService } from './webrtc-media-session.service';
import { WebrtcOrdinaryHealthMonitorService } from './webrtc-ordinary-health-monitor.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import {
  DEFAULT_SEMANTIC_MEDIA_TRANSPORT_POLICY,
  SFU_COOLDOWN_MS,
  SemanticMediaTransportDecision,
  SemanticMediaTransportPolicyPort,
  SemanticMediaTransportSignals,
  SemanticMediaTransportReason,
} from './semantic-media-transport-state-machine';
import { SfuBroadcastParticipantCapService } from './sfu-broadcast-participant-cap.service';

export const SEMANTIC_MEDIA_TRANSPORT_POLICY = new InjectionToken<SemanticMediaTransportPolicyPort>(
  'SEMANTIC_MEDIA_TRANSPORT_POLICY',
  { providedIn: 'root', factory: () => DEFAULT_SEMANTIC_MEDIA_TRANSPORT_POLICY },
);

export const SEMANTIC_MEDIA_PATH_CLOCK = new InjectionToken<() => number>(
  'SEMANTIC_MEDIA_PATH_CLOCK',
  { providedIn: 'root', factory: () => Date.now },
);

export interface SemanticSfuPathContext {
  readonly hubUrl: string;
  readonly tenantId: string;
  readonly sessionId: string;
  readonly membershipEpoch: number;
  readonly localPeerId: string;
  readonly remotePeerIds: readonly string[];
  readonly featureEnabled: boolean;
}

export interface SemanticSfuPathResult {
  readonly effectivePath: 'ordinary' | 'sfu';
  readonly reasonCode: string;
}

/**
 * Shell-scoped orchestration adapter.  It composes Hub CAS admission, pair
 * key derivation and LiveKit track switching without moving policy authority
 * into the browser. Pair publication discovery uses the encrypted pair
 * channel; bounded groups use Hub-signed key epochs and opaque Relay delivery.
 */
@Injectable()
export class SemanticSfuPathCoordinatorService implements OnDestroy {
  private readonly api = inject(SemanticSfuAdmissionApiService);
  private readonly peerKeys = inject(WebrtcPeerKeyService);
  private readonly encryption = inject(E2eEncryptionService);
  private readonly media = inject(WebrtcMediaSessionService);
  private readonly ordinaryHealth = inject(WebrtcOrdinaryHealthMonitorService);
  private readonly sfu = inject(LivekitSfuTransportService);
  private readonly signaling = inject(SemanticSfuPairSignalingService);
  private readonly groupKeys = inject(SemanticSfuGroupKeyService);
  private readonly transportPolicy = inject(SEMANTIC_MEDIA_TRANSPORT_POLICY);
  private readonly clock = inject(SEMANTIC_MEDIA_PATH_CLOCK);
  private readonly participantCaps = inject(SfuBroadcastParticipantCapService);
  private readonly subscriptions = new Subscription();
  readonly transportState$ = new BehaviorSubject<SemanticMediaTransportDecision>(
    this.transportPolicy.initial(this.safeClock()),
  );
  private context: SemanticSfuPathContext | null = null;
  private serial = 0;
  private generation = 0;
  private switchedFromOrdinary = false;
  private restoring = false;
  private outgoingOperation = false;
  private incomingOperation = false;
  private readonly desiredGroupReceivers = new Set<string>();
  private groupPollHandle: ReturnType<typeof setInterval> | null = null;
  private groupCursor = '';
  private operationTail: Promise<void> = Promise.resolve();
  private restorationQueued = false;
  private destroyed = false;
  private readonly effectivePaths = new Map<string, 'ordinary' | 'sfu'>();

  constructor() {
    this.subscriptions.add(this.sfu.state$.subscribe(state => {
      if (state.status === 'fallback' && this.switchedFromOrdinary && !this.restoring) {
        this.queueRestoreOrdinary('sfu_transport_fallback');
      }
    }));
    this.subscriptions.add(this.signaling.publicationHint$.subscribe(hint => {
      void this.acceptPublicationHint(hint).catch(() => undefined);
    }));
    this.subscriptions.add(this.sfu.subscriberLost$.subscribe(value => {
      const context = this.context;
      if (context && context.remotePeerIds.length > 1 && context.remotePeerIds.includes(value.receiverId)) {
        this.effectivePaths.set(value.receiverId, 'ordinary');
        if (![...this.effectivePaths.values()].includes('sfu')) {
          this.queueRestoreOrdinary('sfu_subscriber_disconnected');
        } else if (this.media.audioState$.value.status === 'idle') {
          void this.media.requestMicrophone().catch(() => undefined);
          this.switchedFromOrdinary = false;
        }
      } else if (this.switchedFromOrdinary && context?.remotePeerIds.includes(value.receiverId) && !this.restoring) {
        this.effectivePaths.set(value.receiverId, 'ordinary');
        this.queueRestoreOrdinary('sfu_subscriber_disconnected');
      }
    }));
  }

  bind(context: SemanticSfuPathContext | null): void {
    const next = context ? Object.freeze({
      ...context,
      remotePeerIds: Object.freeze([...new Set(context.remotePeerIds)].sort()),
    }) : null;
    if (contextKey(next) === contextKey(this.context)) {
      this.tryBindSignaling();
      this.startGroupPolling();
      return;
    }
    const previous = this.context;
    this.context = next;
    this.transportState$.next(this.transportPolicy.initial(this.nextPolicyTime()));
    this.effectivePaths.clear();
    for (const receiverId of next?.remotePeerIds ?? []) this.effectivePaths.set(receiverId, 'ordinary');
    if (previous) {
      for (const peerId of previous.remotePeerIds) {
        this.ordinaryHealth.reset(previous.sessionId, peerId);
      }
    }
    this.signaling.clear();
    this.tryBindSignaling();
    this.stopGroupPolling();
    this.groupCursor = '';
    for (const receiverId of [...this.desiredGroupReceivers]) {
      if (!next?.remotePeerIds.includes(receiverId)) this.desiredGroupReceivers.delete(receiverId);
    }
    const generation = ++this.generation;
    this.groupKeys.clear();
    this.switchedFromOrdinary = false;
    void this.enqueue(async () => {
      await Promise.all([
        previous ? this.leave(previous).catch(() => undefined) : Promise.resolve(),
        this.sfu.disconnect('sfu_context_changed'),
      ]);
      if (this.context !== next || this.generation !== generation) return;
      if (next?.featureEnabled && next.remotePeerIds.length > 1) {
        await firstValueFrom(this.api.state(next.hubUrl, next.sessionId, next.membershipEpoch));
        if (this.context !== next || this.generation !== generation) return;
      }
      this.startGroupPolling();
      if (!next || next.remotePeerIds.length <= 1 || !this.desiredGroupReceivers.size) return;
      await this.reconcileGroupPublication(next, generation);
    }).catch(() => {
      if (this.context === next && this.media.audioState$.value.status === 'idle') {
        void this.media.requestMicrophone().catch(() => undefined);
      }
    });
  }

  async switchReceiver(receiverId: string, desired: 'auto' | 'ordinary' | 'sfu'): Promise<SemanticSfuPathResult> {
    return this.enqueue(() => this.applyReceiverPreference(receiverId, desired));
  }

  private async applyReceiverPreference(
    receiverId: string,
    desired: 'auto' | 'ordinary' | 'sfu',
  ): Promise<SemanticSfuPathResult> {
    const context = this.requireContext(receiverId);
    if (context.remotePeerIds.length > 1) return this.switchGroupReceiver(context, receiverId, desired);
    const generation = ++this.generation;
    if (desired !== 'sfu') {
      if (this.effectivePaths.get(receiverId) === 'ordinary' && this.sfu.state$.value.status === 'idle') {
        this.reduceTransport(context, {
          admitted: false, sfuHealthy: false, qualityHealthy: true,
          userPreference: 'ordinary', revoked: false,
        });
        return Object.freeze({ effectivePath: 'ordinary', reasonCode: 'receiver_path_ordinary_user_selected' });
      }
      this.beginOrdinaryTransition(context, 'user_selected_ordinary');
      await this.leave(context).catch(() => undefined);
      await this.sfu.disconnect('sfu_receiver_ordinary');
      this.completeOrdinaryTransition(context, 'user_selected_ordinary');
      if (this.switchedFromOrdinary && this.media.audioState$.value.status === 'idle') {
        await this.media.requestMicrophone();
      }
      this.switchedFromOrdinary = false;
      this.effectivePaths.set(receiverId, 'ordinary');
      return Object.freeze({ effectivePath: 'ordinary', reasonCode: 'receiver_path_ordinary_user_selected' });
    }
    if (this.effectivePaths.get(receiverId) === 'sfu'
        && this.transportState$.value.mode === 'sfu_active'
        && this.sfu.state$.value.status === 'connected') {
      return Object.freeze({ effectivePath: 'sfu', reasonCode: 'receiver_path_sfu_already_active' });
    }
    if (this.cooldownActive()) {
      return Object.freeze({ effectivePath: 'ordinary', reasonCode: 'sfu_cooldown_active' });
    }
    const capability = strictSfuE2eeCapability();
    if (!context.featureEnabled || capability !== 'supported') {
      const fallback = this.reduceTransport(context, {
        admitted: false, sfuHealthy: false, qualityHealthy: true,
        userPreference: 'sfu', revoked: false, capability,
      });
      this.effectivePaths.set(receiverId, 'ordinary');
      return Object.freeze({ effectivePath: 'ordinary', reasonCode: fallback.reasonCode });
    }
    const pair = this.peerKeys.requireBinding(true);
    if (
      pair.scopeId !== context.sessionId
      || pair.epoch !== context.membershipEpoch
      || pair.localPeerId !== context.localPeerId
      || pair.remotePeerId !== receiverId
      || pair.tenantId.length === 0
    ) throw new Error('sfu_confirmed_pair_binding_required');

    await this.ordinaryHealth.requireReady(
      context.sessionId,
      receiverId,
      () => this.assertCurrent(context, generation),
    );

    this.signaling.bind();
    let clone: MediaStreamTrack | null = null;
    this.outgoingOperation = true;
    try {
      if (this.media.audioState$.value.status !== 'active' && this.media.audioState$.value.status !== 'muted') {
        await this.media.requestMicrophone();
      }
      clone = this.media.cloneActiveMicrophoneTrack();
      let state = await firstValueFrom(this.api.state(context.hubUrl, context.sessionId, context.membershipEpoch));
      if (state.joined) {
        await firstValueFrom(this.api.leave(context.hubUrl, this.mutation(context, state.revision, 'leave')));
        state = await firstValueFrom(this.api.state(context.hubUrl, context.sessionId, context.membershipEpoch));
      }
      this.assertCurrent(context, generation);
      const joined = await firstValueFrom(this.api.join(
        context.hubUrl, this.mutation(context, state.revision, 'join'), true,
      ));
      const publicationId = `mic-${context.membershipEpoch}-${crypto.randomUUID()}`;
      const publicationToken = await firstValueFrom(this.api.authorizePublication(
        context.hubUrl,
        this.mutation(context, joined.revision, 'publish'),
        {
          publicationId,
          source: 'microphone',
          kind: 'audio',
          privacy: 'ordinary',
          audienceParticipantId: null,
          authorizedSubscriberIds: [receiverId],
          constraints: { max_bitrate_bps: 128_000, max_width: 0, max_height: 0, max_fps: 0 },
        },
      ));
      this.assertCurrent(context, generation);
      const publication = publicationToken.publication;
      if (!publication || !publication.authorized_subscriber_ids.includes(receiverId)) {
        throw new Error('sfu_publication_admission_missing');
      }
      const admission = this.clientAdmission(publicationToken, publication);
      const key = await this.encryption.derivePurposeKeyMaterial(
        pair, 'semantic-sfu-media', publicationToken.roomId,
      );
      try {
        const selected = await this.sfu.connect(admission, key, capability);
        if (selected !== 'sfu') throw new Error(this.sfu.state$.value.reasonCode || 'sfu_connect_failed');
      } finally {
        key.fill(0);
      }
      this.assertCurrent(context, generation);
      this.beginSfuTransition(context);
      if (this.media.audioState$.value.status !== 'idle') {
        this.media.stopAudio('sfu_path_transition_connecting');
        this.switchedFromOrdinary = true;
      }
      await this.sfu.publish(publicationId, clone);
      clone = null;
      this.advanceSfuTransition(context);
      await this.signaling.sendPublicationHint(
        publicationId,
        publicationToken.roomId,
        Math.min(publicationToken.expiresAt * 1000, Date.now() + 30_000),
      );
      if (!await this.sfu.waitForSubscriber(publicationId, receiverId)) {
        throw new Error('sfu_receiver_subscription_unconfirmed');
      }
      this.completeSfuTransition(context);
      this.switchedFromOrdinary = true;
      this.effectivePaths.set(receiverId, 'sfu');
      return Object.freeze({ effectivePath: 'sfu', reasonCode: 'receiver_path_sfu_admitted' });
    } catch (error) {
      safeStop(clone);
      this.beginOrdinaryTransition(context, 'sfu_unhealthy');
      await this.sfu.disconnect('sfu_activation_failed');
      await this.leave(context).catch(() => undefined);
      this.completeOrdinaryTransition(context, 'sfu_unhealthy');
      if (this.media.audioState$.value.status === 'idle') {
        await this.media.requestMicrophone().catch(() => undefined);
      }
      this.switchedFromOrdinary = false;
      this.effectivePaths.set(receiverId, 'ordinary');
      throw error;
    } finally {
      this.outgoingOperation = false;
    }
  }

  private async switchGroupReceiver(
    context: SemanticSfuPathContext,
    receiverId: string,
    desired: 'auto' | 'ordinary' | 'sfu',
  ): Promise<SemanticSfuPathResult> {
    if (!context.tenantId) {
      throw new Error('sfu_group_size_invalid');
    }
    this.participantCaps.enforceCurrentReceiverCountIfResolved(context.remotePeerIds.length);
    const generation = ++this.generation;
    if (desired === 'sfu') {
      if (this.effectivePaths.get(receiverId) === 'sfu'
          && this.sfu.state$.value.status === 'connected') {
        return Object.freeze({ effectivePath: 'sfu', reasonCode: 'receiver_path_sfu_already_active' });
      }
      if (this.cooldownActive()) {
        return Object.freeze({ effectivePath: 'ordinary', reasonCode: 'sfu_cooldown_active' });
      }
      const capability = strictSfuE2eeCapability();
      if (!context.featureEnabled || capability !== 'supported') {
        const fallback = this.reduceTransport(context, {
          admitted: false, sfuHealthy: false, qualityHealthy: true,
          userPreference: 'sfu', revoked: false, capability,
        });
        this.effectivePaths.set(receiverId, 'ordinary');
        return Object.freeze({ effectivePath: 'ordinary', reasonCode: fallback.reasonCode });
      }
      await this.ordinaryHealth.requireReady(
        context.sessionId,
        receiverId,
        () => this.assertCurrent(context, generation),
      );
      this.desiredGroupReceivers.add(receiverId);
    } else {
      this.desiredGroupReceivers.delete(receiverId);
      this.effectivePaths.set(receiverId, 'ordinary');
    }
    if (!this.desiredGroupReceivers.size) {
      this.beginOrdinaryTransition(context, 'user_selected_ordinary');
      await this.leave(context).catch(() => undefined);
      await this.sfu.disconnect('sfu_group_all_receivers_ordinary');
      this.completeOrdinaryTransition(context, 'user_selected_ordinary');
      this.groupKeys.clear();
      if (this.media.audioState$.value.status === 'idle') await this.media.requestMicrophone();
      this.switchedFromOrdinary = false;
      for (const id of context.remotePeerIds) this.effectivePaths.set(id, 'ordinary');
      return Object.freeze({ effectivePath: 'ordinary', reasonCode: 'receiver_path_ordinary_user_selected' });
    }
    const acknowledged = await this.reconcileGroupPublication(context, generation);
    if (desired !== 'sfu') {
      return Object.freeze({ effectivePath: 'ordinary', reasonCode: 'receiver_path_ordinary_user_selected' });
    }
    if (!acknowledged.has(receiverId)) throw new Error('sfu_receiver_subscription_unconfirmed');
    for (const id of acknowledged) this.effectivePaths.set(id, 'sfu');
    return Object.freeze({ effectivePath: 'sfu', reasonCode: 'receiver_path_sfu_admitted' });
  }

  private async reconcileGroupPublication(
    context: SemanticSfuPathContext,
    generation: number,
  ): Promise<ReadonlySet<string>> {
    if (this.outgoingOperation) throw new Error('sfu_group_operation_in_progress');
    const receiverIds = [...this.desiredGroupReceivers].filter(value => context.remotePeerIds.includes(value)).sort();
    if (!receiverIds.length) {
      throw new Error('sfu_group_size_invalid');
    }
    const topologyTransition = this.transportState$.value.mode !== 'sfu_active';
    const allRemoteDesired = context.remotePeerIds.every(value => receiverIds.includes(value));
    let clone: MediaStreamTrack | null = null;
    this.outgoingOperation = true;
    try {
      await this.sfu.disconnect('sfu_group_rekey');
      await this.leave(context).catch(() => undefined);
      if (!['active', 'muted'].includes(this.media.audioState$.value.status)) await this.media.requestMicrophone();
      clone = this.media.cloneActiveMicrophoneTrack();
      const state = await firstValueFrom(this.api.state(context.hubUrl, context.sessionId, context.membershipEpoch));
      this.participantCaps.enforceReceiverCount(state.roomId, receiverIds.length);
      this.assertCurrent(context, generation);
      const joined = await firstValueFrom(this.api.join(
        context.hubUrl, this.mutation(context, state.revision, 'group-join'), true,
      ));
      const publicationId = `mic-${context.membershipEpoch}-${crypto.randomUUID()}`;
      const token = await firstValueFrom(this.api.authorizePublication(
        context.hubUrl,
        this.mutation(context, joined.revision, 'group-publish'),
        {
          publicationId,
          source: 'microphone',
          kind: 'audio',
          privacy: 'ordinary',
          audienceParticipantId: null,
          authorizedSubscriberIds: receiverIds,
          constraints: { max_bitrate_bps: 128_000, max_width: 0, max_height: 0, max_fps: 0 },
        },
      ));
      const publication = token.publication;
      if (!publication
          || publication.authorized_subscriber_ids.join('\0') !== receiverIds.join('\0')) {
        throw new Error('sfu_group_publication_admission_mismatch');
      }
      this.assertCurrent(context, generation);
      const prepared = await this.groupKeys.createPublisherEpoch(
        this.groupContext(context), publicationId, [context.localPeerId, ...receiverIds],
      );
      try {
        const selected = await this.sfu.connect(this.clientAdmission(token, publication), prepared.keyMaterial, 'supported');
        if (selected !== 'sfu') throw new Error(this.sfu.state$.value.reasonCode || 'sfu_connect_failed');
      } finally {
        prepared.keyMaterial.fill(0);
      }
      this.assertCurrent(context, generation);
      if (topologyTransition) this.beginSfuTransition(context);
      if (allRemoteDesired && this.media.audioState$.value.status !== 'idle') {
        this.media.stopAudio('sfu_group_path_transition_connecting');
        this.switchedFromOrdinary = true;
      }
      await this.sfu.publish(publicationId, clone);
      clone = null;
      if (topologyTransition) this.advanceSfuTransition(context);
      const acknowledged = await this.waitForGroupAcknowledgements(
        context, generation, prepared.authorization.authorization_id, publicationId, receiverIds,
      );
      if (topologyTransition) this.completeSfuTransition(context);
      const allRemoteOnSfu = context.remotePeerIds.every(value => acknowledged.has(value));
      if (allRemoteOnSfu && this.media.audioState$.value.status !== 'idle') {
        this.media.stopAudio('sfu_group_path_active');
        this.switchedFromOrdinary = true;
      } else {
        this.switchedFromOrdinary = false;
      }
      for (const receiverId of acknowledged) this.effectivePaths.set(receiverId, 'sfu');
      for (const receiverId of context.remotePeerIds) {
        if (!acknowledged.has(receiverId)) this.effectivePaths.set(receiverId, 'ordinary');
      }
      return acknowledged;
    } catch (error) {
      safeStop(clone);
      this.beginOrdinaryTransition(context, 'sfu_unhealthy');
      await this.sfu.disconnect('sfu_group_activation_failed');
      await this.leave(context).catch(() => undefined);
      this.completeOrdinaryTransition(context, 'sfu_unhealthy');
      if (this.media.audioState$.value.status === 'idle') await this.media.requestMicrophone().catch(() => undefined);
      this.switchedFromOrdinary = false;
      for (const receiverId of context.remotePeerIds) this.effectivePaths.set(receiverId, 'ordinary');
      throw error;
    } finally {
      this.outgoingOperation = false;
    }
  }

  private async waitForGroupAcknowledgements(
    context: SemanticSfuPathContext,
    generation: number,
    authorizationId: string,
    publicationId: string,
    receiverIds: readonly string[],
  ): Promise<ReadonlySet<string>> {
    const deadline = Date.now() + 15_000;
    const acknowledged = new Set<string>();
    do {
      this.assertCurrent(context, generation);
      const status = await this.groupKeys.status(this.groupContext(context), authorizationId);
      for (const receiverId of status.acknowledgedMemberIds) {
        if (!receiverIds.includes(receiverId) || acknowledged.has(receiverId)) continue;
        acknowledged.add(receiverId);
        this.sfu.confirmAuthorizedSubscriber(publicationId, receiverId);
      }
      if (acknowledged.size === receiverIds.length) break;
      await delay(250);
    } while (Date.now() < deadline);
    return acknowledged;
  }

  stop(reasonCode = 'sfu_program_stopped'): Promise<void> {
    this.generation += 1;
    return this.enqueue(() => this.stopCurrent(reasonCode));
  }

  private async stopCurrent(reasonCode: string): Promise<void> {
    const context = this.context;
    this.stopGroupPolling();
    if (context) this.beginOrdinaryTransition(context, 'sfu_revoked');
    if (context) await this.leave(context).catch(() => undefined);
    await this.sfu.disconnect(reasonCode);
    if (context) this.completeOrdinaryTransition(context, 'sfu_revoked');
    this.groupKeys.clear();
    this.desiredGroupReceivers.clear();
    for (const receiverId of context?.remotePeerIds ?? []) this.effectivePaths.set(receiverId, 'ordinary');
    this.switchedFromOrdinary = false;
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    const context = this.context;
    this.context = null;
    this.signaling.clear();
    this.stopGroupPolling();
    this.groupKeys.clear();
    this.generation += 1;
    if (context) void this.leave(context).catch(() => undefined);
    void this.sfu.disconnect('sfu_coordinator_destroyed');
    this.subscriptions.unsubscribe();
    this.transportState$.complete();
  }

  private requireContext(receiverId: string): SemanticSfuPathContext {
    const context = this.context;
    if (!context || !context.remotePeerIds.includes(receiverId)) throw new Error('sfu_session_context_missing');
    return context;
  }

  private mutation(context: SemanticSfuPathContext, revision: number, operation: string) {
    return {
      sessionId: context.sessionId,
      membershipEpoch: context.membershipEpoch,
      expectedRevision: revision,
      idempotencyKey: `sfu-${operation}-${++this.serial}-${crypto.randomUUID()}`,
    };
  }

  private clientAdmission(
    token: SemanticSfuToken,
    publication: NonNullable<SemanticSfuToken['publication']>,
  ): SfuClientAdmission {
    return Object.freeze({
      server_url: token.serverUrl,
      access_token: token.accessToken,
      room_id: token.roomId,
      membership_epoch: token.membershipEpoch,
      expires_at: token.expiresAt,
      publications: Object.freeze([Object.freeze({
        publication_id: publication.publication_id,
        source: publication.source,
        kind: publication.kind,
        privacy: publication.privacy,
        authorized_subscriber_ids: Object.freeze([...publication.authorized_subscriber_ids]),
      })]),
      subscription_publication_ids: Object.freeze([]),
      strict_e2ee: true,
    });
  }

  private async leave(context: SemanticSfuPathContext): Promise<void> {
    const state: SemanticSfuState = await firstValueFrom(
      this.api.state(context.hubUrl, context.sessionId, context.membershipEpoch),
    );
    if (!state.joined) return;
    await firstValueFrom(this.api.leave(context.hubUrl, this.mutation(context, state.revision, 'leave')));
  }

  private assertCurrent(context: SemanticSfuPathContext, generation: number): void {
    if (this.context !== context || this.generation !== generation) throw new Error('sfu_operation_superseded');
  }

  private async restoreOrdinary(_reasonCode: string): Promise<void> {
    if (this.restoring) return;
    this.restoring = true;
    try {
      const context = this.context;
      if (context) this.beginOrdinaryTransition(context, 'sfu_unhealthy');
      await this.sfu.disconnect('sfu_subscriber_fallback');
      if (context) await this.leave(context).catch(() => undefined);
      if (context) {
        this.completeOrdinaryTransition(context, 'sfu_unhealthy');
        for (const receiverId of context.remotePeerIds) this.effectivePaths.set(receiverId, 'ordinary');
      }
      if (this.media.audioState$.value.status === 'idle') await this.media.requestMicrophone().catch(() => undefined);
      this.switchedFromOrdinary = false;
    } finally {
      this.restoring = false;
    }
  }

  private queueRestoreOrdinary(reasonCode: string): void {
    if (this.restorationQueued || this.destroyed) return;
    this.restorationQueued = true;
    this.generation += 1;
    void this.enqueue(() => this.restoreOrdinary(reasonCode)).finally(() => {
      this.restorationQueued = false;
    });
  }

  private tryBindSignaling(): void {
    if (!this.context || this.context.remotePeerIds.length !== 1) return;
    try { this.signaling.bind(); } catch { this.signaling.clear(); }
  }

  private startGroupPolling(): void {
    this.stopGroupPolling();
    const context = this.context;
    if (!context || !context.featureEnabled || context.remotePeerIds.length < 2
      || !this.participantCaps.permitsCurrentReceiverCount(context.remotePeerIds.length) || !context.tenantId
      || strictSfuE2eeCapability() !== 'supported') return;
    void this.pollGroupPackages();
    this.groupPollHandle = setInterval(() => { void this.pollGroupPackages(); }, 1_000);
  }

  private stopGroupPolling(): void {
    if (this.groupPollHandle !== null) clearInterval(this.groupPollHandle);
    this.groupPollHandle = null;
  }

  private async pollGroupPackages(): Promise<void> {
    const context = this.context;
    if (!context || this.incomingOperation || this.outgoingOperation || context.remotePeerIds.length < 2) return;
    this.incomingOperation = true;
    const generation = this.generation;
    try {
      const page = await this.groupKeys.receiveAvailable(this.groupContext(context), this.groupCursor);
      this.assertCurrent(context, generation);
      if (page.installed) await this.acceptGroupEpoch(context, generation, page.installed);
      this.groupCursor = page.cursor;
    } catch {
      // Fail closed and retain the cursor so a transient Hub/SFU outage can retry.
    } finally {
      this.incomingOperation = false;
    }
  }

  private async acceptGroupEpoch(
    context: SemanticSfuPathContext,
    generation: number,
    installed: ReceivedSfuGroupEpoch,
  ): Promise<void> {
    if (!context.remotePeerIds.includes(installed.publisherId)
        || !installed.authorization.member_ids.includes(context.localPeerId)) {
      installed.keyMaterial.fill(0);
      throw new Error('sfu_group_delivery_audience_mismatch');
    }
    try {
      await this.ordinaryHealth.requireReady(
        context.sessionId,
        installed.publisherId,
        () => this.assertCurrent(context, generation),
      );
      await this.sfu.disconnect('sfu_group_subscription_rekey');
      await this.leave(context).catch(() => undefined);
      const state = await firstValueFrom(this.api.state(
        context.hubUrl, context.sessionId, context.membershipEpoch,
      ));
      this.assertCurrent(context, generation);
      const joined = await firstValueFrom(this.api.join(
        context.hubUrl, this.mutation(context, state.revision, 'group-sub-join'), true,
      ));
      const token = await firstValueFrom(this.api.authorizeSubscription(
        context.hubUrl,
        this.mutation(context, joined.revision, 'group-subscribe'),
        `sub-${context.membershipEpoch}-${crypto.randomUUID()}`,
        installed.authorization.publication_id,
      ));
      const subscription = token.subscription;
      if (!subscription
          || subscription.publisher_id !== installed.publisherId
          || subscription.publication_id !== installed.authorization.publication_id
          || token.roomId !== installed.authorization.room_id) {
        throw new Error('sfu_group_subscription_admission_mismatch');
      }
      this.assertCurrent(context, generation);
      const selected = await this.sfu.connect(
        this.subscriptionAdmission(token, installed.authorization.publication_id),
        installed.keyMaterial,
        'supported',
      );
      if (selected !== 'sfu') throw new Error(this.sfu.state$.value.reasonCode || 'sfu_connect_failed');
      this.beginSfuTransition(context);
      this.advanceSfuTransition(context);
      await this.groupKeys.acknowledge(this.groupContext(context), installed);
      this.completeSfuTransition(context);
      this.effectivePaths.set(installed.publisherId, 'sfu');
    } catch (error) {
      this.groupKeys.purge(installed.authorization);
      this.beginOrdinaryTransition(context, 'sfu_unhealthy');
      await this.sfu.disconnect('sfu_group_subscription_failed');
      await this.leave(context).catch(() => undefined);
      this.completeOrdinaryTransition(context, 'sfu_unhealthy');
      this.effectivePaths.set(installed.publisherId, 'ordinary');
      throw error;
    } finally {
      installed.keyMaterial.fill(0);
    }
  }

  private groupContext(context: SemanticSfuPathContext): SemanticSfuGroupContext {
    return Object.freeze({
      hubUrl: context.hubUrl,
      tenantId: context.tenantId,
      sessionId: context.sessionId,
      membershipEpoch: context.membershipEpoch,
      localPeerId: context.localPeerId,
    });
  }

  private async acceptPublicationHint(hint: SemanticSfuPublicationHint): Promise<void> {
    const context = this.context;
    if (!context || !context.featureEnabled || this.outgoingOperation || this.incomingOperation
        || context.remotePeerIds.length !== 1 || hint.sender_id !== context.remotePeerIds[0]
        || hint.audience_id !== context.localPeerId || hint.session_id !== context.sessionId
        || hint.epoch !== context.membershipEpoch || hint.expires_at_ms <= Date.now()
        || this.sfu.state$.value.status === 'connected' || strictSfuE2eeCapability() !== 'supported') return;
    const pair = this.peerKeys.requireBinding(true);
    if (pair.scopeId !== context.sessionId || pair.epoch !== context.membershipEpoch
        || pair.localPeerId !== context.localPeerId || pair.remotePeerId !== hint.sender_id) return;
    this.incomingOperation = true;
    const generation = this.generation;
    try {
      await this.ordinaryHealth.requireReady(
        context.sessionId,
        hint.sender_id,
        () => this.assertCurrent(context, generation),
      );
      let state = await firstValueFrom(this.api.state(context.hubUrl, context.sessionId, context.membershipEpoch));
      if (state.joined) {
        await firstValueFrom(this.api.leave(context.hubUrl, this.mutation(context, state.revision, 'leave')));
        state = await firstValueFrom(this.api.state(context.hubUrl, context.sessionId, context.membershipEpoch));
      }
      this.assertCurrent(context, generation);
      const joined = await firstValueFrom(this.api.join(
        context.hubUrl, this.mutation(context, state.revision, 'join'), true,
      ));
      const subscriptionId = `sub-${context.membershipEpoch}-${crypto.randomUUID()}`;
      const token = await firstValueFrom(this.api.authorizeSubscription(
        context.hubUrl,
        this.mutation(context, joined.revision, 'subscribe'),
        subscriptionId,
        hint.publication_id,
      ));
      const subscription = token.subscription;
      if (!subscription || subscription.publication_id !== hint.publication_id
          || subscription.publisher_id !== hint.sender_id || token.roomId !== hint.room_id) {
        throw new Error('sfu_subscription_admission_mismatch');
      }
      this.assertCurrent(context, generation);
      const key = await this.encryption.derivePurposeKeyMaterial(pair, 'semantic-sfu-media', token.roomId);
      try {
        const selected = await this.sfu.connect(this.subscriptionAdmission(token, hint.publication_id), key, 'supported');
        if (selected !== 'sfu') throw new Error(this.sfu.state$.value.reasonCode || 'sfu_connect_failed');
      } finally {
        key.fill(0);
      }
      this.beginSfuTransition(context);
      this.advanceSfuTransition(context);
      this.completeSfuTransition(context);
      this.effectivePaths.set(hint.sender_id, 'sfu');
    } catch {
      this.beginOrdinaryTransition(context, 'sfu_unhealthy');
      await this.sfu.disconnect('sfu_incoming_subscription_failed');
      await this.leave(context).catch(() => undefined);
      this.completeOrdinaryTransition(context, 'sfu_unhealthy');
      this.effectivePaths.set(hint.sender_id, 'ordinary');
    } finally {
      this.incomingOperation = false;
    }
  }

  private subscriptionAdmission(token: SemanticSfuToken, publicationId: string): SfuClientAdmission {
    return Object.freeze({
      server_url: token.serverUrl,
      access_token: token.accessToken,
      room_id: token.roomId,
      membership_epoch: token.membershipEpoch,
      expires_at: token.expiresAt,
      publications: Object.freeze([]),
      subscription_publication_ids: Object.freeze([publicationId]),
      strict_e2ee: true,
    });
  }

  private beginSfuTransition(context: SemanticSfuPathContext): void {
    const decision = this.reduceTransport(context, {
      admitted: true, sfuHealthy: true, qualityHealthy: true,
      userPreference: 'sfu', revoked: false,
    });
    if (decision.mode !== 'sfu_connecting' && decision.mode !== 'sfu_active') {
      throw new Error(decision.mode === 'ordinary_cooldown' ? 'sfu_cooldown_active' : decision.reasonCode);
    }
  }

  private advanceSfuTransition(context: SemanticSfuPathContext): void {
    const decision = this.reduceTransport(context, {
      admitted: true, sfuHealthy: true, qualityHealthy: true,
      userPreference: 'sfu', revoked: false,
    });
    if (decision.mode !== 'sfu_connecting' && decision.mode !== 'sfu_active') {
      throw new Error(decision.reasonCode);
    }
  }

  private completeSfuTransition(context: SemanticSfuPathContext): void {
    this.advanceSfuTransition(context);
    if (this.transportState$.value.mode !== 'sfu_active') throw new Error('sfu_hysteresis_pending');
  }

  private beginOrdinaryTransition(
    context: SemanticSfuPathContext,
    reasonCode: Extract<SemanticMediaTransportReason, 'user_selected_ordinary' | 'sfu_unhealthy' | 'sfu_revoked'>,
  ): void {
    this.reduceTransport(context, ordinarySignals(reasonCode));
  }

  private completeOrdinaryTransition(
    context: SemanticSfuPathContext,
    reasonCode: Extract<SemanticMediaTransportReason, 'user_selected_ordinary' | 'sfu_unhealthy' | 'sfu_revoked'>,
  ): void {
    const decision = this.reduceTransport(context, ordinarySignals(reasonCode));
    if (decision.sfuBulkEnabled && decision.ordinaryBulkEnabled) throw new Error('semantic_transport_overlap');
  }

  private reduceTransport(
    context: SemanticSfuPathContext,
    values: Pick<SemanticMediaTransportSignals,
      'admitted' | 'sfuHealthy' | 'qualityHealthy' | 'userPreference' | 'revoked'>
      & Partial<Pick<SemanticMediaTransportSignals, 'capability'>>,
  ): SemanticMediaTransportDecision {
    const capability = values.capability ?? strictSfuE2eeCapability();
    const next = this.transportPolicy.reduce(this.transportState$.value, {
      nowMs: this.nextPolicyTime(),
      enabled: context.featureEnabled,
      capability,
      admitted: values.admitted,
      sfuHealthy: values.sfuHealthy,
      e2eeReady: capability === 'supported',
      qualityHealthy: values.qualityHealthy,
      userPreference: values.userPreference,
      revoked: values.revoked,
    });
    if (next.ordinaryBulkEnabled && next.sfuBulkEnabled) throw new Error('semantic_transport_overlap');
    this.transportState$.next(next);
    return next;
  }

  private safeClock(): number {
    const value = Math.trunc(this.clock());
    return Number.isSafeInteger(value) && value >= 0 ? value : 0;
  }

  private cooldownActive(): boolean {
    const state = this.transportState$.value;
    return state.mode === 'ordinary_cooldown' && this.safeClock() - state.enteredAtMs < SFU_COOLDOWN_MS;
  }

  private nextPolicyTime(): number {
    const last = this.transportState$?.value.lastSignalAtMs ?? -1;
    return Math.max(this.safeClock(), last + 1);
  }

  private enqueue<T>(operation: () => Promise<T>): Promise<T> {
    if (this.destroyed) return Promise.reject(new Error('sfu_coordinator_destroyed'));
    const run = this.operationTail.then(operation, operation);
    this.operationTail = run.then(() => undefined, () => undefined);
    return run;
  }
}

function contextKey(value: SemanticSfuPathContext | null): string {
  return value ? [
    value.hubUrl, value.tenantId, value.sessionId, value.membershipEpoch, value.localPeerId,
    ...value.remotePeerIds, value.featureEnabled,
  ].join('\0') : '';
}

function strictSfuE2eeCapability(): 'supported' | 'unknown' {
  return typeof globalThis.Worker === 'function' && Boolean(globalThis.crypto?.subtle) ? 'supported' : 'unknown';
}

function ordinarySignals(
  reasonCode: Extract<SemanticMediaTransportReason, 'user_selected_ordinary' | 'sfu_unhealthy' | 'sfu_revoked'>,
): Pick<SemanticMediaTransportSignals,
  'admitted' | 'sfuHealthy' | 'qualityHealthy' | 'userPreference' | 'revoked'> {
  return {
    admitted: false,
    sfuHealthy: reasonCode !== 'sfu_unhealthy',
    qualityHealthy: reasonCode !== 'sfu_unhealthy',
    userPreference: reasonCode === 'user_selected_ordinary' ? 'ordinary' : 'auto',
    revoked: reasonCode === 'sfu_revoked',
  };
}

function safeStop(track: MediaStreamTrack | null): void {
  try { track?.stop(); } catch { /* deterministic cleanup */ }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, milliseconds));
}
