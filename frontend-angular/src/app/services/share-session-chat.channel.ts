import { BehaviorSubject, firstValueFrom } from 'rxjs';

import type { HubApiCoreService } from './hub-api-core.service';
import type { PairPublicSessionContractPolicy } from './pair-public-session-contract.policy';
import type { PairSecureSequenceService } from './pair-secure-sequence.service';
import type { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import type { PairViewCryptoPort } from './pair-view-crypto.service';
import type { WebrtcTransportService } from './webrtc-transport.service';
import type { ActiveShareState, ShareChatMessage, ShareSession, StrictShareChatWireMessage } from './share-session.types';
import {
  chatMessageFromStrictPlaintext,
  createStrictChatPlaintext,
  newShareChatMessageId,
  parseLegacyChat,
  parseStrictChatPlaintext,
  parseStrictChatWire,
} from './share-session-chat.codec';

/** Session state and rules of ShareSessionService the chat channel reads. */
export interface ShareSessionChatContext {
  readonly state$: BehaviorSubject<ActiveShareState>;
  currentGeneration(): number;
  isCurrentSession(sessionId: string, generation: number): boolean;
  isStrictSession(session: ShareSession): boolean;
  hasChatPermission(): boolean;
  canSendChat(): boolean;
  currentUserId(): string;
  hubUrl(): string;
  closeUnverifiedStrictTransport(): void;
}

/** Infrastructure ports the chat channel sends and receives through. */
export interface ShareSessionChatPorts {
  readonly core: Pick<HubApiCoreService, 'post'>;
  readonly transport: Pick<WebrtcTransportService, 'mode$' | 'send'>;
  readonly cryptoPort: Pick<PairViewCryptoPort, 'seal' | 'open' | 'ready'>;
  readonly secureSequences: Pick<PairSecureSequenceService, 'next'>;
  readonly controlPlane: Pick<PairSessionControlPlaneService, 'isPublicSession' | 'assertSessionAvailable'>;
  readonly publicContract: Pick<PairPublicSessionContractPolicy, 'assertValid'>;
}

/**
 * Pair chat of the active share session: strict E2EE sealing/opening,
 * legacy plaintext compatibility, the Hub relay boundary (never for Public
 * sessions) and the bounded message list in the session read model.
 * Session lifecycle, polling and generations stay in ShareSessionService.
 */
export class ShareSessionChatChannel {
  constructor(
    private readonly context: ShareSessionChatContext,
    private readonly ports: ShareSessionChatPorts,
  ) {}

  /** One `chat` message received over the pair transport. */
  receiveTransportChat(payload: unknown, sessionId: string): void {
    const session = this.context.state$.value.session;
    if (!session || sessionId !== session.id) return;
    if (this.ports.controlPlane.isPublicSession(session.id)) {
      try { this.ports.publicContract.assertValid(session); } catch {
        this.context.closeUnverifiedStrictTransport();
        return;
      }
    }
    if (session && this.context.isStrictSession(session)) {
      void this.acceptStrictChatWire(
        payload,
        session.id,
        this.context.currentGeneration(),
      ).catch(() => undefined);
      return;
    }
    const item = parseLegacyChat(payload, this.context.state$.value.session?.id);
    if (item) this.appendMessage(item);
  }

  async send(text: string): Promise<void> {
    const { session } = this.context.state$.value;
    const normalized = text.trim();
    if (!session || !normalized) return;
    if (!this.context.hasChatPermission()) throw new Error('chat_permission_required');
    const publicSession = this.ports.controlPlane.isPublicSession(session.id);
    if (publicSession) {
      this.ports.controlPlane.assertSessionAvailable(session.id);
      this.ports.publicContract.assertValid(session);
    }

    if (this.context.isStrictSession(session)) {
      if (!this.context.canSendChat() || !session.security_epoch) {
        throw new Error('confirmed_pair_binding_required');
      }
      const senderUserId = this.context.currentUserId();
      const id = newShareChatMessageId();
      const plaintext = createStrictChatPlaintext(id, session.id, senderUserId, normalized, Date.now() / 1000);
      const encryptedPayload = await this.ports.cryptoPort.seal(JSON.stringify(plaintext), {
        scopeId: session.id,
        epoch: session.security_epoch,
        sequence: await this.ports.secureSequences.next(
          session.id,
          session.security_epoch,
          senderUserId,
          'semantic',
        ),
        payloadType: 'pair.chat_message',
        trafficClass: 'semantic',
      });
      const wire: StrictShareChatWireMessage = { id, encrypted_payload: encryptedPayload };
      if (this.ports.transport.mode$.value === 'webrtc') {
        this.ports.transport.send('chat', wire);
      } else {
        if (publicSession) {
          throw new Error('public_pair_datachannel_required');
        }
        this.assertHubPayloadRelayAllowed(session.id);
        const url = this.context.hubUrl();
        if (!url) throw new Error('hub_unavailable');
        await firstValueFrom(this.ports.core.post(
          `${url}/share-sessions/${session.id}/chat/messages`, wire, url,
        ));
      }
      this.appendMessage(chatMessageFromStrictPlaintext(plaintext));
      return;
    }

    if (this.ports.transport.mode$.value === 'webrtc') {
      this.ports.transport.send('chat', {
        id: newShareChatMessageId(),
        session_id: session.id,
        text: normalized,
        sender_id: this.context.currentUserId(),
        created_at: Date.now() / 1000,
      });
      return;
    }

    this.assertHubPayloadRelayAllowed(session.id);
    const url = this.context.hubUrl();
    if (!url) throw new Error('hub_unavailable');
    await firstValueFrom(this.ports.core.post(`${url}/share-sessions/${session.id}/chat/messages`, {
      text: normalized, visibility: 'room', channel_type: 'room',
      id: newShareChatMessageId(),
    }, url));
  }

  /** Applies one polled Hub relay page and advances the opaque cursor. */
  async acceptPage(
    session: ShareSession,
    rawMessages: unknown[],
    cursor: string,
    generation: number,
  ): Promise<void> {
    if (!this.context.isCurrentSession(session.id, generation)) return;
    if (this.context.isStrictSession(session)) {
      for (const raw of rawMessages) {
        if (!this.context.isCurrentSession(session.id, generation)) return;
        try {
          await this.acceptStrictChatWire(raw, session.id, generation);
        } catch { /* reject and advance the opaque relay cursor */ }
      }
    } else {
      for (const raw of rawMessages) {
        if (!this.context.isCurrentSession(session.id, generation)) return;
        const item = parseLegacyChat(raw, this.context.state$.value.session?.id);
        if (item) this.appendMessage(item);
      }
    }
    if (this.context.isCurrentSession(session.id, generation)) {
      this.context.state$.next({ ...this.context.state$.value, cursor });
    }
  }

  /** Public sessions have no application-payload relay through the Hub. */
  assertHubPayloadRelayAllowed(sessionId: string): void {
    if (this.ports.controlPlane.isPublicSession(sessionId)) {
      throw new Error('public_pair_hub_relay_forbidden');
    }
    this.ports.controlPlane.assertSessionAvailable(sessionId);
  }

  private async acceptStrictChatWire(
    raw: unknown,
    expectedSessionId: string,
    generation: number,
  ): Promise<void> {
    const wire = parseStrictChatWire(raw);
    const session = this.context.state$.value.session;
    if (
      !wire
      || !session
      || session.id !== expectedSessionId
      || !this.context.isCurrentSession(expectedSessionId, generation)
      || !this.context.isStrictSession(session)
      || !session.security_epoch
    ) return;
    if (!this.context.hasChatPermission() || !this.ports.cryptoPort.ready(session.id, session.security_epoch)) return;
    const opened = await this.ports.cryptoPort.open(wire.encrypted_payload, {
      scopeId: session.id,
      epoch: session.security_epoch,
    });
    if (!this.context.isCurrentSession(expectedSessionId, generation)) return;
    if (opened.payloadType !== 'pair.chat_message') return;
    let rawPlaintext: unknown;
    try { rawPlaintext = JSON.parse(opened.plaintext); } catch { return; }
    const plaintext = parseStrictChatPlaintext(rawPlaintext);
    if (
      !plaintext
      || plaintext.id !== wire.id
      || plaintext.sessionId !== session.id
      || plaintext.senderUserId !== opened.senderId
    ) return;
    this.appendMessage(chatMessageFromStrictPlaintext(plaintext));
  }

  private appendMessage(item: ShareChatMessage): void {
    const current = this.context.state$.value;
    if (!current.session || item.session_id !== current.session.id) return;
    if (current.messages.some((message) => message.id === item.id)) return;
    this.context.state$.next({ ...current, messages: [...current.messages, item].slice(-200) });
  }
}
