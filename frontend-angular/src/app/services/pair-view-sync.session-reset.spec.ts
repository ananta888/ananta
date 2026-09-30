/**
 * Pins the observable session reset contract of bindSession/unbindSession.
 *
 * The split of PairViewSyncService let bind/unbind reuse the shared
 * clearLocalViewSendState/cancelCursorSend resets (a few counters and timers
 * are now reset twice on rebind). These specs fix the pre-split behavior:
 * no timer or in-flight send of a previous binding escapes, a rebind starts
 * a fresh snapshot baseline and rate budget, stream emission order is stable,
 * and the control grant keeps its original lifetime.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { BehaviorSubject, Subject } from 'rxjs';

import { PairViewSyncService } from './pair-view-sync.service';
import { SharedViewStateService } from './shared-view-state.service';
import { WebrtcTransportService } from './webrtc-transport.service';
import { ShareSessionService } from './share-session.service';
import { PAIR_VIEW_CRYPTO, PairViewCryptoPort } from './pair-view-crypto.service';
import { PairSecureSequenceService } from './pair-secure-sequence.service';
import {
  ControlMessage,
  DEFAULT_PERMISSIONS,
  PermissionSet,
  RelayEnvelope,
} from './pair-view-sync.types';

class ResetTransport {
  readonly viewTransportState$ = new BehaviorSubject({
    sessionId: 'sess-a', semanticEpoch: 1, generation: 1, ready: false,
  });
  readonly message$ = new Subject<{ type: string; session_id: string; payload: unknown }>();
  readonly sentView: RelayEnvelope[] = [];
  send(): void { /* legacy relay path unused */ }
  sendView(envelope: RelayEnvelope): boolean {
    this.sentView.push(envelope);
    return true;
  }
  emitControl(msg: ControlMessage, sessionId: string): void {
    this.message$.next({
      type: 'view_payload', session_id: sessionId,
      payload: { encrypted_payload: `TEST1::${JSON.stringify(msg)}` },
    });
  }
}

class ResetCrypto implements PairViewCryptoPort {
  readonly sealedPlaintexts: string[] = [];
  private blocking = false;
  private readonly resolvers: Array<() => void> = [];
  ready(_scope: string, epoch: number): boolean { return epoch === 1; }
  block(): void { this.blocking = true; }
  unblock(): void { this.blocking = false; }
  releaseNext(): void { this.resolvers.shift()?.(); }
  async seal(plaintext: string): Promise<string> {
    this.sealedPlaintexts.push(plaintext);
    if (this.blocking) await new Promise<void>(resolve => this.resolvers.push(resolve));
    return `TEST1::${plaintext}`;
  }
  async open(serialized: string): Promise<any> {
    const plaintext = serialized.slice('TEST1::'.length);
    const value = JSON.parse(plaintext);
    return {
      plaintext,
      payloadType: 'pair.control',
      senderId: value?.senderUserId ?? 'peer',
      sequence: 1,
    };
  }
  clear(): void {}
}

class ResetShare {
  readonly perms: PermissionSet = {
    ...DEFAULT_PERMISSIONS,
    view_tui: true,
    remote_cursor: true,
    remote_control: true,
  };
  readonly state$ = new BehaviorSubject<any>({
    session: { id: 'sess-a', security_epoch: 1 }, role: 'owner', participants: [], messages: [], cursor: '0',
  });
  readonly currentUserId = 'owner-a';
  currentPermissions(): PermissionSet { return this.perms; }
}

function setup() {
  TestBed.configureTestingModule({ providers: [provideRouter([])] });
  const transport = new ResetTransport();
  const crypto = new ResetCrypto();
  let sequence = 0;
  TestBed.overrideProvider(WebrtcTransportService, { useValue: transport });
  TestBed.overrideProvider(ShareSessionService, { useValue: new ResetShare() });
  TestBed.overrideProvider(PAIR_VIEW_CRYPTO, { useValue: crypto });
  TestBed.overrideProvider(PairSecureSequenceService, { useValue: {
    next: async () => { sequence += 1; return sequence; },
    clearScope: () => undefined,
  } });
  const sync = TestBed.runInInjectionContext(() => new PairViewSyncService());
  const view = TestBed.inject(SharedViewStateService);
  return { transport, crypto, sync, view };
}

function bindWithConsent(sync: PairViewSyncService, sessionId: string, cursor = false): void {
  sync.bindSession(sessionId, 'owner-a', 1);
  sync.setLocalCompactSharing(sessionId, 1, { view: true, cursor });
}

const tick = (ms = 0) => new Promise(resolve => setTimeout(resolve, ms));

function controlMessage(kind: ControlMessage['kind'], sessionId = 'sess-a'): ControlMessage {
  return { sessionId, senderUserId: 'peer', kind, grantToken: kind === 'grant' ? 'token-1' : null, createdAt: Date.now() };
}

describe('PairViewSyncService session reset on bind/unbind', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('drops a debounced delta that was scheduled before unbind', async () => {
    const { transport, sync, view } = setup();
    bindWithConsent(sync, 'sess-a');
    await tick();
    const before = transport.sentView.length;
    expect(before).toBe(1);

    view.updatePartial({ activeTab: 'pending-before-unbind' });
    sync.unbindSession();
    await tick(150);

    expect(transport.sentView).toHaveLength(before);
  });

  it('drops a scheduled cursor dispatch after unbind', async () => {
    const { transport, sync } = setup();
    sync.bindSession('sess-a', 'owner-a', 1);
    sync.setLocalCompactSharing('sess-a', 1, { view: false, cursor: true });
    for (let index = 0; index < 20; index += 1) {
      sync.sendCursor('Peer', { line: null, column: null, nx: index / 20, ny: 0.5 });
    }
    await tick();
    const cursorsBefore = transport.sentView.filter(envelope => envelope.kind === 'cursor').length;

    sync.unbindSession();
    await tick(150);

    expect(transport.sentView.filter(envelope => envelope.kind === 'cursor')).toHaveLength(cursorsBefore);
  });

  it('never delivers a seal that was in flight for the previous binding', async () => {
    const { transport, crypto, sync } = setup();
    crypto.block();
    bindWithConsent(sync, 'sess-a');
    await tick();
    expect(crypto.sealedPlaintexts).toHaveLength(1);

    crypto.unblock();
    sync.bindSession('sess-b', 'owner-a', 1);
    crypto.releaseNext();
    await tick();

    expect(transport.sentView).toHaveLength(0);
    sync.setLocalCompactSharing('sess-b', 1, { view: true, cursor: false });
    await tick();
    expect(transport.sentView).toHaveLength(1);
    expect(transport.sentView[0].kind).toBe('snapshot');
    expect(crypto.sealedPlaintexts.at(-1)).toContain('"sessionId":"sess-b"');
  });

  it('starts a rebound session from a fresh snapshot baseline and delta budget', async () => {
    const { transport, sync, view } = setup();
    bindWithConsent(sync, 'sess-a');
    await tick();
    for (let index = 0; index < 5; index += 1) {
      view.updatePartial({ activeTab: `a-${index}` });
      await tick(80);
    }
    const sentForA = transport.sentView.length;
    expect(transport.sentView.slice(1).every(envelope => envelope.kind !== 'snapshot')).toBe(true);

    sync.unbindSession();
    bindWithConsent(sync, 'sess-b');
    await tick();
    expect(transport.sentView[sentForA]?.kind).toBe('snapshot');

    view.updatePartial({ activeTab: 'b-1' });
    await tick(150);
    expect(transport.sentView).toHaveLength(sentForA + 2);
    expect(transport.sentView.at(-1)?.kind).not.toBe('snapshot');
    sync.unbindSession();
  });

  it('keeps the unbind emission order of cursors, remote views and local sharing', () => {
    const { sync } = setup();
    bindWithConsent(sync, 'sess-a', true);
    const events: string[] = [];
    const subs = [
      sync.peerCursors$.subscribe(map => events.push(`cursors:${map.size}:view=${sync.isLocalViewSharingEnabled}`)),
      sync.remoteViews$.subscribe(map => events.push(`views:${map.size}:view=${sync.isLocalViewSharingEnabled}`)),
      sync.localCompactSharing$.subscribe(state => events.push(`sharing:${state.view}/${state.cursor}/${state.pending}`)),
    ];
    events.length = 0;

    sync.unbindSession();

    expect(events).toEqual([
      'cursors:0:view=true',
      'views:0:view=true',
      'sharing:false/false/false',
    ]);
    subs.forEach(sub => sub.unsubscribe());
  });

  it('keeps the rebind emission order and resets follow mode without emitting', () => {
    const { sync } = setup();
    bindWithConsent(sync, 'sess-a', true);
    sync.setFollowMode('active');
    const events: string[] = [];
    const subs = [
      sync.peerCursors$.subscribe(map => events.push(`cursors:${map.size}`)),
      sync.remoteViews$.subscribe(map => events.push(`views:${map.size}`)),
      sync.localCompactSharing$.subscribe(state => events.push(`sharing:${state.view}/${state.cursor}`)),
      sync.followMode$.subscribe(mode => events.push(`follow:${mode}`)),
    ];
    events.length = 0;

    sync.bindSession('sess-b', 'owner-a', 1);

    expect(events).toEqual(['cursors:0', 'views:0', 'sharing:false/false', 'views:0']);
    expect(sync.getFollowMode()).toBe('paused');
    expect(sync.isLocalViewSharingEnabled).toBe(false);
    expect(sync.isLocalCursorSharingEnabled).toBe(false);
    subs.forEach(sub => sub.unsubscribe());
    sync.unbindSession();
  });

  it('keeps an accepted control grant until the next bind and requires a fresh request after it', async () => {
    const { transport, sync } = setup();
    bindWithConsent(sync, 'sess-a');
    sync.requestControl();
    transport.emitControl(controlMessage('grant'), 'sess-a');
    await tick();
    expect(sync.hasControlGrant()).toBe(true);

    sync.unbindSession();
    expect(sync.hasControlGrant()).toBe(true);

    sync.bindSession('sess-a', 'owner-a', 1);
    expect(sync.hasControlGrant()).toBe(false);
    transport.emitControl(controlMessage('grant'), 'sess-a');
    await tick();
    expect(sync.hasControlGrant()).toBe(false);
    sync.unbindSession();
  });
});
