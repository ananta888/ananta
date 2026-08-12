/**
 * T14: Pair-View-Sync reconnect scenarios.
 *
 * These specs cover what happens when the transport drops and
 * the user comes back: the service must:
 *  - keep accepting incoming messages from the old transport
 *    after a `unbind`/`bind` cycle (fresh state)
 *  - drop messages tagged with a stale session id
 *  - drop messages whose baseHash does not match the current
 *    local view hash (the engine triggers a snapshot request)
 *  - request a snapshot whenever the receiver detects a base-
 *    hash mismatch on a view-payload message
 *  - the peer-cursor map is cleared on unbind so a new
 *    session does not inherit stale cursors
 */
import { describe, expect, it } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { BehaviorSubject, Subject } from 'rxjs';

import { PairViewSyncService, PeerCursor } from './pair-view-sync.service';
import { SharedViewStateService } from './shared-view-state.service';
import { ViewDeltaService } from './view-delta.service';
import { WebrtcTransportService } from './webrtc-transport.service';
import { ShareSessionService } from './share-session.service';
import { PAIR_VIEW_CRYPTO, PairViewCryptoPort } from './pair-view-crypto.service';
import { PairSecureSequenceService } from './pair-secure-sequence.service';
import {
  DEFAULT_PERMISSIONS,
  PermissionSet,
  RelayEnvelope,
  SharedViewState,
  ViewStateDelta,
  PAIR_VIEW_SYNC_VERSION,
} from './pair-view-sync.types';

class FakeTransport {
  viewTransportState$ = new BehaviorSubject({
    sessionId: 'sess-A', semanticEpoch: 1, generation: 1, ready: false,
  });
  message$ = new Subject<{ type: string; session_id: string; payload: unknown }>();
  sent: Array<{ type: string; payload: unknown }> = [];
  sentView: RelayEnvelope[] = [];
  send(type: string, payload: unknown): void { this.sent.push({ type, payload }); }
  sendView(e: RelayEnvelope): boolean { this.sentView.push(e); return true; }
  emitView(env: RelayEnvelope, sessionId: string): void {
    this.message$.next({ type: 'view_payload', session_id: sessionId, payload: env });
  }
  emitCursorMessage(msg: PeerCursor, sessionId: string): void {
    this.message$.next({
      type: 'view_payload',
      session_id: sessionId,
      payload: { encrypted_payload: `STUB1::${JSON.stringify({
        sessionId, senderUserId: msg.userId, userLabel: msg.userLabel,
        cursor: msg.cursor, sentAt: msg.lastSeenAt,
      })}` },
    });
  }
  emitSnapshotRequest(sessionId: string): void {
    this.message$.next({ type: 'snapshot_request', session_id: sessionId, payload: null });
  }
}

class FakeCrypto implements PairViewCryptoPort {
  ready(_scope: string, epoch: number): boolean { return epoch === 1; }
  async seal(plaintext: string): Promise<string> { return `TEST1::${plaintext}`; }
  async open(serialized: string): Promise<any> {
    const prefix = serialized.startsWith('TEST1::') ? 'TEST1::' : 'STUB1::';
    const plaintext = serialized.slice(prefix.length);
    const value = JSON.parse(plaintext);
    return {
      plaintext,
      payloadType: value?.userLabel ? 'pair.cursor' : 'pair.view_delta',
      senderId: value?.senderUserId ?? 'partner-x',
      sequence: value?.seq ?? 1,
    };
  }
  clear(): void {}
}

class FakeShare {
  perms: PermissionSet | null = {
    ...DEFAULT_PERMISSIONS,
    view_tui: true,
    remote_control: false,
    remote_cursor: true,
  };
  readonly state$ = new BehaviorSubject<any>({
    session: { id: 'sess-A', security_epoch: 1 }, role: 'owner', participants: [], messages: [], cursor: '0',
  });
  readonly currentUserId = 'owner-1';
  currentPermissions(): PermissionSet | null { return this.perms; }
  setPerms(p: PermissionSet | null): void { this.perms = p; }
}

function setup() {
  TestBed.configureTestingModule({ providers: [provideRouter([])] });
  const transport = new FakeTransport();
  const share = new FakeShare();
  let secureSequence = 0;
  TestBed.overrideProvider(WebrtcTransportService, { useValue: transport });
  TestBed.overrideProvider(ShareSessionService, { useValue: share });
  TestBed.overrideProvider(PAIR_VIEW_CRYPTO, { useValue: new FakeCrypto() });
  TestBed.overrideProvider(PairSecureSequenceService, { useValue: {
    next: async () => { secureSequence += 1; return secureSequence; },
    clearScope: () => undefined,
  } });
  const sync = TestBed.runInInjectionContext(() => new PairViewSyncService());
  const view = TestBed.inject(SharedViewStateService);
  const delta = TestBed.inject(ViewDeltaService);
  return { transport, share, sync, view, delta };
}

function envFor(delta: ViewStateDelta): RelayEnvelope {
  return {
    message_id: 'm1', kind: delta.kind,
    base_hash: delta.baseHash, new_hash: delta.newHash,
    width: 0, height: 0,
    encrypted_payload: `STUB1::${JSON.stringify(delta)}`,
  };
}

function makeDelta(sessionId: string, senderUserId: string, seq: number, baseHash: string, newHash: string, kind: ViewStateDelta['kind'] = 'delta'): ViewStateDelta {
  return {
    version: PAIR_VIEW_SYNC_VERSION, sessionId, senderUserId, seq,
    baseHash, newHash, kind,
    ops: kind === 'delta' ? [{ op: 'set', path: 'activeTab', value: `peer-tab-${seq}` }] : [],
    createdAt: Date.now(), payload: null,
  };
}

describe('PairViewSyncService (T14 reconnect)', () => {
  it('rejects view-payloads with a stale session id', async () => {
    const { transport, sync, view } = setup();
    sync.bindSession('sess-A', 'owner-1', 1);
    sync.setLocalCompactSharing('sess-A', 1, { view: true, cursor: false });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const initial = transport.sentView[0];
    const state = view.current;
    const d = makeDelta('sess-B', 'partner-x', 99, initial.new_hash, state.viewHash);
    const before = transport.sent.length;
    transport.emitView(envFor(d), 'sess-B');
    expect(transport.sent.length).toBe(before); // nothing sent in response
    sync.unbindSession();
  });

  it('rejects view-payloads whose baseHash mismatches the local hash', async () => {
    const { transport, sync, view } = setup();
    sync.bindSession('sess-A', 'owner-1', 1);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const state = view.current;
    // baseHash is intentionally wrong (a hash the local state never produced)
    const d = makeDelta('sess-A', 'partner-x', 100, 'wrong-hash-1234', state.viewHash);
    const snapshotReqsBefore = transport.sentView.filter((entry) => entry.kind === 'control').length;
    transport.emitView(envFor(d), 'sess-A');
    await new Promise((resolve) => setTimeout(resolve, 0));
    // The engine must request a snapshot
    const snapshotReqsAfter = transport.sentView.filter((entry) => entry.kind === 'control').length;
    expect(snapshotReqsAfter).toBe(snapshotReqsBefore + 1);
    sync.unbindSession();
  });

  it('rejects cursor messages with a stale session id', async () => {
    const { transport, sync } = setup();
    sync.bindSession('sess-A', 'owner-1', 1);
    const emitted: PeerCursor[] = [];
    const sub = sync.peerCursors$.subscribe((m) => { for (const v of m.values()) emitted.push(v); });
    // First, emit a valid cursor so peer-cursor map is populated
    transport.emitCursorMessage({
      userId: 'partner-A', userLabel: 'P',
      cursor: { line: null, column: null, nx: 0.1, ny: 0.2 },
      lastSeenAt: Date.now(),
    }, 'sess-A');
    // Now a stale-session cursor
    transport.emitCursorMessage({
      userId: 'partner-A', userLabel: 'P',
      cursor: { line: null, column: null, nx: 0.9, ny: 0.9 },
      lastSeenAt: Date.now(),
    }, 'sess-Z');
    await new Promise((resolve) => setTimeout(resolve, 0));
    const found = emitted.find((c) => c.cursor.nx === 0.9 && c.cursor.ny === 0.9);
    expect(found).toBeUndefined();
    sub.unsubscribe();
    sync.unbindSession();
  });

  it('clears peer-cursor map on unbindSession so a new session does not inherit stale cursors', () => {
    const { transport, sync } = setup();
    sync.bindSession('sess-A', 'owner-1', 1);
    const emits: Array<ReadonlyMap<string, PeerCursor>> = [];
    const sub = sync.peerCursors$.subscribe((m) => { emits.push(m); });
    transport.emitCursorMessage({
      userId: 'partner-A', userLabel: 'P',
      cursor: { line: null, column: null, nx: 0.11, ny: 0.22 },
      lastSeenAt: Date.now(),
    }, 'sess-A');
    // Re-bind -> a different session
    sync.unbindSession();
    // The most recent emit must be the empty map produced by unbind
    const last = emits[emits.length - 1];
    expect(last).toBeDefined();
    expect(last.size).toBe(0);
    sub.unsubscribe();
    sync.unbindSession();
  });

  it('keeps accepting view-payloads across an unbind+bind cycle (fresh session id)', async () => {
    const { transport, sync, view } = setup();
    sync.bindSession('sess-A', 'owner-1', 1);
    await new Promise((resolve) => setTimeout(resolve, 0));
    // Change something
    view.updatePartial({ activeTab: 'first' });
    sync.unbindSession();
    sync.bindSession('sess-B', 'owner-2', 1);
    sync.setLocalCompactSharing('sess-B', 1, { view: true, cursor: false });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const peerState: SharedViewState = {
      ...view.current,
      sessionId: 'sess-B', ownerUserId: 'partner-y', seq: 1,
      activeTab: 'partner-ready',
    };
    const { viewHash: _previousHash, ...hashable } = peerState;
    peerState.viewHash = view.hashOf(hashable);
    const snapshot = TestBed.inject(ViewDeltaService).createSnapshot(peerState);
    // Should accept
    const before = (sync as any).stats.appliesAccepted;
    transport.emitView(envFor(snapshot), 'sess-B');
    await new Promise((resolve) => setTimeout(resolve, 0));
    const after = (sync as any).stats.appliesAccepted;
    expect(after).toBeGreaterThanOrEqual(before + 1);
    sync.unbindSession();
  });

  it('coalesces mismatches from one authenticated sender regardless of attacker-controlled baseHash', async () => {
    const { transport, sync, view } = setup();
    sync.bindSession('sess-A', 'owner-1', 1);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const state = view.current;
    const deltas = [
      makeDelta('sess-A', 'partner-x', 200, 'wrong-hash-a', state.viewHash),
      makeDelta('sess-A', 'partner-x', 201, 'wrong-hash-b', state.viewHash),
      makeDelta('sess-A', 'partner-x', 202, 'wrong-hash-c', state.viewHash),
    ];
    const before = transport.sentView.filter((entry) => entry.kind === 'control').length;
    for (const delta of deltas) transport.emitView(envFor(delta), 'sess-A');
    await new Promise((resolve) => setTimeout(resolve, 0));
    const after = transport.sentView.filter((entry) => entry.kind === 'control').length;
    expect(after - before).toBe(1);

    const peerState: SharedViewState = {
      ...view.current,
      sessionId: 'sess-A', ownerUserId: 'partner-x', seq: 203,
      activeTab: 'peer-baseline',
    };
    const { viewHash: _oldHash, ...hashable } = peerState;
    peerState.viewHash = view.hashOf(hashable);
    const snapshot = TestBed.inject(ViewDeltaService).createSnapshot(peerState);
    transport.emitView(envFor(snapshot), 'sess-A');
    await new Promise(resolve => setTimeout(resolve, 0));

    const nextMismatch = makeDelta(
      'sess-A', 'partner-x', 204, 'different-wrong-hash', peerState.viewHash,
    );
    transport.emitView(envFor(nextMismatch), 'sess-A');
    await new Promise(resolve => setTimeout(resolve, 0));
    const afterValidSnapshot = transport.sentView.filter((entry) => entry.kind === 'control').length;
    expect(afterValidSnapshot - before).toBe(2);
    sync.unbindSession();
  });
});
