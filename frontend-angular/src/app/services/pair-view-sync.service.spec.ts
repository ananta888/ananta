import { describe, expect, it, beforeEach, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { BehaviorSubject, Subject } from 'rxjs';

import { PairViewSyncService } from './pair-view-sync.service';
import { SharedViewStateService } from './shared-view-state.service';
import { ViewDeltaService } from './view-delta.service';
import { WebrtcTransportService } from './webrtc-transport.service';
import { ShareSessionService } from './share-session.service';
import { PAIR_VIEW_CRYPTO, PairViewCryptoPort } from './pair-view-crypto.service';
import { PairSecureSequenceService } from './pair-secure-sequence.service';
import {
  ControlMessage,
  DEFAULT_PERMISSIONS,
  PermissionSet,
  RelayEnvelope,
  SharedViewState,
  ViewStateDelta,
  PAIR_VIEW_SYNC_VERSION,
} from './pair-view-sync.types';

// ── Test doubles ─────────────────────────────────────────────────────

class FakeTransport {
  mode$ = { value: 'webrtc' as 'webrtc' | 'hub_relay' | 'idle' };
  viewTransportState$ = new BehaviorSubject({
    sessionId: 'sess-test', semanticEpoch: 1, generation: 1, ready: false,
  });
  message$ = new Subject<{ type: string; session_id: string; payload: unknown }>();
  sent: Array<{ type: string; payload: unknown }> = [];
  sentView: RelayEnvelope[] = [];
  acceptView = true;
  send(type: string, payload: unknown): void {
    this.sent.push({ type, payload });
  }
  sendView(envelope: RelayEnvelope): boolean {
    if (!this.acceptView) return false;
    this.sentView.push(envelope);
    return true;
  }
  setViewReady(
    ready: boolean,
    sessionId = 'sess-test',
    semanticEpoch = 1,
    generation = 1,
  ): void {
    this.viewTransportState$.next({ sessionId, semanticEpoch, generation, ready });
  }
  // T06
  emitView(viewEnvelope: RelayEnvelope, sessionId: string): void {
    this.message$.next({ type: 'view_payload', session_id: sessionId, payload: viewEnvelope });
  }
  emitControl(msg: ControlMessage, sessionId: string): void {
    this.message$.next({
      type: 'view_payload', session_id: sessionId,
      payload: { encrypted_payload: `TEST1::${JSON.stringify(msg)}` },
    });
  }
  emitSnapshotRequest(sessionId: string): void {
    this.message$.next({ type: 'snapshot_request', session_id: sessionId, payload: null });
  }
}

class FakeCrypto implements PairViewCryptoPort {
  readonly readyEpochs = new Set([1]);
  readonly sealedPlaintexts: string[] = [];
  openCalls = 0;
  private blockNewSeals = false;
  private readonly sealResolvers: Array<() => void> = [];
  ready(_scopeId: string, epoch: number): boolean { return this.readyEpochs.has(epoch); }
  blockSeals(): void { this.blockNewSeals = true; }
  releaseNextSeal(): void { this.sealResolvers.shift()?.(); }
  async seal(plaintext: string): Promise<string> {
    this.sealedPlaintexts.push(plaintext);
    if (this.blockNewSeals) {
      await new Promise<void>(resolve => this.sealResolvers.push(resolve));
    }
    return `TEST1::${plaintext}`;
  }
  async open(serialized: string): Promise<any> {
    this.openCalls += 1;
    const prefix = serialized.startsWith('TEST1::') ? 'TEST1::' : 'STUB1::';
    if (!serialized.startsWith(prefix)) throw new Error('test_ciphertext_invalid');
    const plaintext = serialized.slice(prefix.length);
    const value = JSON.parse(plaintext);
    let payloadType = 'pair.view_delta';
    if (value?.kind && ['request', 'grant', 'revoke', 'request_follow', 'request_unfollow'].includes(value.kind)) payloadType = 'pair.control';
    else if (value?.userLabel) payloadType = 'pair.cursor';
    return { plaintext, payloadType, senderId: value?.senderUserId ?? 'peer', sequence: value?.seq ?? 1 };
  }
  clear(): void {}
}

class FakeShare {
  private perms: PermissionSet | null = {
    ...DEFAULT_PERMISSIONS,
    view_tui: true,
    remote_control: false,
    remote_cursor: false,
  };
  readonly state$ = new BehaviorSubject<any>({
    session: { id: 'sess-test', security_epoch: 1 }, role: 'owner', participants: [], messages: [], cursor: '0',
  });
  currentUserId = 'owner-test';
  currentPermissions(): PermissionSet | null { return this.perms; }
  setPerms(p: PermissionSet | null): void { this.perms = p; }
}

function setup(extra?: { perms?: PermissionSet | null; autoConsent?: boolean }) {
  TestBed.configureTestingModule({
    providers: [provideRouter([])],
  });
  const transport = new FakeTransport();
  const share = new FakeShare();
  let secureSequence = 0;
  if (extra?.perms !== undefined) share.setPerms(extra.perms);

  // Provide fakes for the services that PairViewSync injects.
  TestBed.overrideProvider(WebrtcTransportService, { useValue: transport });
  TestBed.overrideProvider(ShareSessionService, { useValue: share });
  const pairCrypto = new FakeCrypto();
  TestBed.overrideProvider(PAIR_VIEW_CRYPTO, { useValue: pairCrypto });
  TestBed.overrideProvider(PairSecureSequenceService, { useValue: {
    next: async () => { secureSequence += 1; return secureSequence; },
    clearScope: () => undefined,
  } });

  const sync = TestBed.runInInjectionContext(() => new PairViewSyncService());
  const view = TestBed.inject(SharedViewStateService);
  const delta = TestBed.inject(ViewDeltaService);

  // Manually bind, since we don't have a real session response.
  sync.bindSession('sess-test', 'owner-test', 1);
  if (extra?.autoConsent !== false) {
    sync.setLocalCompactSharing('sess-test', 1, { view: true, cursor: true });
  }
  return { transport, share, sync, view, delta, pairCrypto };
}

function decodeEncrypted(enc: string): unknown {
  if (enc.startsWith('TEST1::')) return JSON.parse(enc.slice('TEST1::'.length));
  if (enc.startsWith('STUB1::')) return JSON.parse(enc.slice('STUB1::'.length));
  return null;
}

describe('PairViewSyncService (T05 sendepfad)', () => {
  it('consumes quick-share intent only at the first confirmed peer epoch', async () => {
    const { transport, share, sync, pairCrypto } = setup({
      autoConsent: false,
      perms: { ...DEFAULT_PERMISSIONS, view_tui: true, remote_cursor: true },
    });
    pairCrypto.readyEpochs.clear();

    expect(sync.armLocalCompactSharingOnFirstPeerReady('sess-test')).toBe(true);
    expect(sync.isLocalCompactSharingPending).toBe(true);
    expect(sync.isLocalViewSharingEnabled).toBe(false);

    share.state$.next({ ...share.state$.value, session: { id: 'sess-test', security_epoch: 2 } });
    sync.updateSecurityEpoch(2);
    pairCrypto.readyEpochs.add(2);
    sync.onCryptoReady();
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(sync.isLocalCompactSharingPending).toBe(false);
    expect(sync.isLocalViewSharingEnabled).toBe(true);
    expect(sync.isLocalCursorSharingEnabled).toBe(true);
    expect(transport.sentView).toHaveLength(1);

    sync.updateSecurityEpoch(3);
    pairCrypto.readyEpochs.add(3);
    sync.onCryptoReady();
    expect(sync.isLocalViewSharingEnabled).toBe(false);
    expect(sync.isLocalCursorSharingEnabled).toBe(false);
    expect(transport.sentView).toHaveLength(1);
    sync.unbindSession();
  });

  it('allows the creator to revoke a pending quick-share intent before peer readiness', () => {
    const { sync, pairCrypto } = setup({ autoConsent: false });
    pairCrypto.readyEpochs.clear();
    expect(sync.armLocalCompactSharingOnFirstPeerReady('sess-test')).toBe(true);
    expect(sync.cancelPendingLocalCompactSharing('sess-test')).toBe(true);
    expect(sync.isLocalCompactSharingPending).toBe(false);
    sync.unbindSession();
  });

  it('does not consume a pending quick-share intent after the local identity changes', () => {
    const { share, sync, pairCrypto } = setup({ autoConsent: false });
    pairCrypto.readyEpochs.clear();
    expect(sync.armLocalCompactSharingOnFirstPeerReady('sess-test')).toBe(true);
    share.currentUserId = 'replacement-owner';
    pairCrypto.readyEpochs.add(1);
    sync.onCryptoReady();
    expect(sync.isLocalViewSharingEnabled).toBe(false);
    expect(sync.isLocalCursorSharingEnabled).toBe(false);
    sync.unbindSession();
  });

  it('retries one exact snapshot when the DataChannel opens after crypto readiness', async () => {
    const { transport, sync } = setup({
      autoConsent: false,
      perms: { ...DEFAULT_PERMISSIONS, view_tui: true },
    });
    transport.acceptView = false;

    sync.setLocalCompactSharing('sess-test', 1, { view: true, cursor: false });
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(transport.sentView).toHaveLength(0);

    transport.acceptView = true;
    transport.setViewReady(true);
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(transport.sentView).toHaveLength(1);
    expect(transport.sentView[0]?.kind).toBe('snapshot');

    transport.setViewReady(true);
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(transport.sentView).toHaveLength(1);
    sync.unbindSession();
  });

  it('does not retry a deferred snapshot after consent or transport context changes', async () => {
    const { transport, sync } = setup({
      autoConsent: false,
      perms: { ...DEFAULT_PERMISSIONS, view_tui: true },
    });
    transport.acceptView = false;
    sync.setLocalCompactSharing('sess-test', 1, { view: true, cursor: false });
    await new Promise(resolve => setTimeout(resolve, 0));
    sync.setLocalCompactSharing('sess-test', 1, { view: false, cursor: false });

    transport.acceptView = true;
    transport.setViewReady(true, 'stale-session', 1, 2);
    transport.setViewReady(true, 'sess-test', 2, 3);
    transport.setViewReady(true, 'sess-test', 1, 4);
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(transport.sentView).toHaveLength(0);
    sync.unbindSession();
  });

  it('does not share local view or cursor before exact-session consent', async () => {
    const { transport, view, sync } = setup();
    sync.setLocalCompactSharing('sess-test', 1, { view: false, cursor: false });
    const before = transport.sentView.length;
    view.updatePartial({ activeTab: 'private-tab' });
    sync.sendCursor('Peer-safe', { line: null, column: null, nx: 0.2, ny: 0.3 });
    await new Promise(resolve => setTimeout(resolve, 130));
    expect(transport.sentView.length).toBe(before);
    expect(sync.setLocalCompactSharing('wrong-session', 1, { view: true, cursor: true })).toBe(false);
    sync.unbindSession();
  });

  it('never carries pointer coordinates in view_tui snapshots or deltas', async () => {
    const { transport, view, sync } = setup({
      autoConsent: false,
      perms: { ...DEFAULT_PERMISSIONS, view_tui: true, remote_cursor: false },
    });
    view.updatePartial({
      cursor: { line: 8, column: 13, nx: 0.25, ny: 0.75 },
    });
    sync.setLocalCompactSharing('sess-test', 1, { view: true, cursor: false });
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(transport.sentView).toHaveLength(1);
    const snapshot = decodeEncrypted(transport.sentView[0].encrypted_payload) as ViewStateDelta;
    expect(snapshot.kind).toBe('snapshot');
    expect(snapshot.ops.some(op => op.path === 'cursor')).toBe(false);
    expect(JSON.stringify(snapshot)).not.toContain('"nx"');
    expect(JSON.stringify(snapshot)).not.toContain('"line":8');
    sync.unbindSession();
  });

  it('sends a snapshot on bind', async () => {
    const { transport, sync, pairCrypto } = setup();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(transport.sentView.length).toBeGreaterThanOrEqual(1);
    const first = transport.sentView[0];
    expect(first.kind).toBe('snapshot');
    expect(new TextEncoder().encode(pairCrypto.sealedPlaintexts[0]).byteLength).toBeLessThan(8 * 1024);
    sync.unbindSession();
  });

  it('coalesces pointer bursts to at most 20 encrypted updates per second and keeps the latest point', async () => {
    const { transport, sync } = setup({
      autoConsent: false,
      perms: { ...DEFAULT_PERMISSIONS, remote_cursor: true },
    });
    sync.setLocalCompactSharing('sess-test', 1, { view: false, cursor: true });
    for (let index = 0; index < 100; index += 1) {
      sync.sendCursor('Peer-safe', {
        line: null,
        column: null,
        nx: index / 100,
        ny: index / 100,
      });
    }
    await new Promise(resolve => setTimeout(resolve, 240));
    const cursors = transport.sentView.filter(envelope => envelope.kind === 'cursor');
    expect(cursors.length).toBeGreaterThan(0);
    expect(cursors.length).toBeLessThanOrEqual(5);
    expect(decodeEncrypted(cursors.at(-1)!.encrypted_payload)).toMatchObject({
      cursor: { nx: 0.99, ny: 0.99 },
    });
    sync.unbindSession();
  });

  it('debounces non-scroll deltas', async () => {
    const { transport, view, sync } = setup();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const before = transport.sentView.length;
    view.updatePartial({ activeTab: 'details' });
    view.updatePartial({ activeTab: 'logs' });
    view.updatePartial({ activeTab: 'review' });
    await new Promise((r) => setTimeout(r, 150));
    const after = transport.sentView.length;
    expect(after - before).toBe(1);
    sync.unbindSession();
  });

  it('rate-limits burst to MAX_DELTAS_PER_SECOND', async () => {
    const { transport, view, sync } = setup();
    const before = transport.sentView.length;
    for (let i = 0; i < 50; i++) {
      view.updatePartial({ activeTab: `tab-${i}` });
    }
    await new Promise((r) => setTimeout(r, 300));
    // We must not have flooded the transport
    const sent = transport.sentView.length - before;
    expect(sent).toBeLessThanOrEqual(20);
    sync.unbindSession();
  });

  it('serializes async view sealing and coalesces to the latest pending state', async () => {
    const { transport, view, sync, pairCrypto } = setup({ autoConsent: false });
    pairCrypto.blockSeals();
    sync.setLocalCompactSharing('sess-test', 1, { view: true, cursor: false });
    await Promise.resolve();
    await Promise.resolve();
    expect(pairCrypto.sealedPlaintexts).toHaveLength(1);

    view.updatePartial({ activeTab: 'intermediate' });
    view.updatePartial({ activeTab: 'latest' });
    await new Promise(resolve => setTimeout(resolve, 120));
    expect(pairCrypto.sealedPlaintexts).toHaveLength(1);

    pairCrypto.releaseNextSeal();
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(pairCrypto.sealedPlaintexts).toHaveLength(2);
    expect(pairCrypto.sealedPlaintexts[1]).toContain('latest');
    pairCrypto.releaseNextSeal();
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(transport.sentView).toHaveLength(2);
    sync.unbindSession();
  });

  it('drops an in-flight encrypted view after local consent is revoked', async () => {
    const { transport, sync, pairCrypto } = setup({
      autoConsent: false,
      perms: { ...DEFAULT_PERMISSIONS, view_tui: true, remote_cursor: true },
    });
    pairCrypto.blockSeals();
    sync.setLocalCompactSharing('sess-test', 1, { view: true, cursor: false });
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(pairCrypto.sealedPlaintexts).toHaveLength(1);
    sync.setLocalCompactSharing('sess-test', 1, { view: false, cursor: false });
    pairCrypto.releaseNextSeal();
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(transport.sentView).toHaveLength(0);
    sync.unbindSession();
  });

  it('drops an in-flight encrypted cursor after local consent is revoked', async () => {
    const { transport, sync, pairCrypto } = setup({
      autoConsent: false,
      perms: { ...DEFAULT_PERMISSIONS, remote_cursor: true },
    });
    pairCrypto.blockSeals();
    sync.setLocalCompactSharing('sess-test', 1, { view: false, cursor: true });
    sync.sendCursor('Peer-safe', { line: null, column: null, nx: 0.25, ny: 0.5 });
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(pairCrypto.sealedPlaintexts).toHaveLength(1);
    sync.setLocalCompactSharing('sess-test', 1, { view: false, cursor: false });
    pairCrypto.releaseNextSeal();
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(transport.sentView).toHaveLength(0);
    sync.unbindSession();
  });

  it('scroll is throttled (no more than 1 message per SCROLL_THROTTLE_MS)', async () => {
    const { transport, view, sync } = setup();
    const before = transport.sentView.length;
    for (let i = 0; i < 10; i++) {
      view.updateScroll({ x: i, y: i * 10 });
    }
    await new Promise((r) => setTimeout(r, 250));
    const scrollMsgs = transport.sentView.slice(before).filter((e) => e.kind === 'scroll');
    expect(scrollMsgs.length).toBeLessThanOrEqual(3);
    sync.unbindSession();
  });
});

describe('PairViewSyncService (T07 apply path)', () => {
  it('projects a valid snapshot by authenticated peer without mutating local state', async () => {
    const { transport, view, sync } = setup();
    const initialViewHash = view.current.viewHash;
    const peerState: SharedViewState = {
      ...view.current,
      sessionId: 'sess-test', ownerUserId: 'peer', seq: 999,
      route: '/dashboard', queryParams: {}, activeSurface: 'dashboard', activeTab: 'goals',
    };
    const { viewHash: _old, ...hashable } = peerState;
    peerState.viewHash = view.hashOf(hashable);
    const snapshot = TestBed.inject(ViewDeltaService).createSnapshot(peerState);
    const projections: any[] = [];
    const sub = sync.remoteViews$.subscribe(value => projections.push(value));
    // Construct a valid snapshot envelope to apply
    const enc = `STUB1::${JSON.stringify(snapshot)}`;
    transport.emitView({
      message_id: 'm1', kind: 'snapshot', base_hash: snapshot.baseHash, new_hash: snapshot.newHash,
      width: 0, height: 0, encrypted_payload: enc,
    }, 'sess-test');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(view.current.viewHash).toBe(initialViewHash);
    expect(projections.at(-1).get('peer').state.route).toBe('/dashboard');
    expect(projections.at(-1).get('peer').state.activeTab).toBe('goals');
    sub.unsubscribe();
    sync.unbindSession();
  });

  it('keeps each authenticated peer projection monotonic when an older snapshot arrives late', async () => {
    const { transport, view, sync, delta } = setup();
    const projections: Array<ReadonlyMap<string, any>> = [];
    const sub = sync.remoteViews$.subscribe(value => projections.push(value));
    const newer: SharedViewState = {
      ...view.current,
      sessionId: 'sess-test', ownerUserId: 'peer', seq: 10,
      route: '/newer', queryParams: {}, activeSurface: 'dashboard', activeTab: 'newer',
    };
    const { viewHash: _newerHash, ...newerHashable } = newer;
    newer.viewHash = view.hashOf(newerHashable);
    const older: SharedViewState = {
      ...newer,
      seq: 9,
      route: '/older',
      activeTab: 'older',
    };
    const { viewHash: _olderHash, ...olderHashable } = older;
    older.viewHash = view.hashOf(olderHashable);

    for (const [messageId, snapshot] of [
      ['newer-snapshot', delta.createSnapshot(newer)],
      ['older-snapshot', delta.createSnapshot(older)],
    ] as const) {
      transport.emitView({
        message_id: messageId,
        kind: 'snapshot',
        base_hash: snapshot.baseHash,
        new_hash: snapshot.newHash,
        width: 0,
        height: 0,
        encrypted_payload: `STUB1::${JSON.stringify(snapshot)}`,
      }, 'sess-test');
      await new Promise(resolve => setTimeout(resolve, 0));
    }

    expect(projections.at(-1)?.get('peer')?.state.route).toBe('/newer');
    expect(projections.at(-1)?.get('peer')?.state.seq).toBe(10);
    sub.unsubscribe();
    sync.unbindSession();
  });

  it('redacts artifact identifiers unless artifact_share is granted', async () => {
    const { transport, view, sync } = setup({
      perms: { ...DEFAULT_PERMISSIONS, view_tui: true, artifact_share: false },
    });
    await new Promise(resolve => setTimeout(resolve, 0));
    view.updatePartial({
      activeArtifactId: 'secret-artifact',
      activeArtifactHash: 'secret-hash',
      activeFilePath: '/private/file.ts',
      activeSymbolId: 'PrivateSymbol',
    });
    await new Promise(resolve => setTimeout(resolve, 120));
    const plaintexts = transport.sentView.map(item => item.encrypted_payload);
    expect(plaintexts.join('\n')).not.toContain('secret-artifact');
    expect(plaintexts.join('\n')).not.toContain('/private/file.ts');
    sync.unbindSession();
  });

  it('drops envelopes with mismatched sessionId', () => {
    const { transport, view, sync } = setup();
    const seq0 = view.current.seq;
    const enc = `STUB1::${JSON.stringify({
      version: PAIR_VIEW_SYNC_VERSION,
      sessionId: 'sess-other',
      senderUserId: 'peer', seq: 1, baseHash: '', newHash: 'h',
      kind: 'snapshot', ops: [], createdAt: Date.now(),
    })}`;
    transport.emitView({
      message_id: 'm2', kind: 'snapshot', base_hash: '', new_hash: 'h',
      width: 0, height: 0, encrypted_payload: enc,
    }, 'sess-other');
    expect(view.current.seq).toBe(seq0); // unchanged
    sync.unbindSession();
  });

  it('drops envelopes that fail validator', () => {
    const { transport, sync } = setup();
    // Garbage payload
    transport.emitView({
      message_id: 'm3', kind: 'snapshot', base_hash: '', new_hash: 'h',
      width: 0, height: 0, encrypted_payload: 'STUB1::not-json',
    }, 'sess-test');
    // No exception = good
    sync.unbindSession();
  });

  it('rejects oversized encrypted input before invoking crypto', async () => {
    const { transport, sync, pairCrypto } = setup();
    const before = pairCrypto.openCalls;
    transport.emitView({
      message_id: 'oversized', kind: 'delta', base_hash: 'base', new_hash: 'next',
      width: 0, height: 0, encrypted_payload: 'x'.repeat(64 * 1024 + 1),
    }, 'sess-test');
    await Promise.resolve();
    expect(pairCrypto.openCalls).toBe(before);
    sync.unbindSession();
  });
});

describe('PairViewSyncService (T12 control default-deny)', () => {
  it('denies a control request when permission is not granted', async () => {
    const { transport, sync } = setup({ perms: { ...DEFAULT_PERMISSIONS, remote_control: false } });
    const before = transport.sent.filter((s) => s.type === 'control').length;
    transport.emitControl({
      sessionId: 'sess-test', senderUserId: 'peer', kind: 'request', grantToken: null, createdAt: Date.now(),
    }, 'sess-test');
    await new Promise((resolve) => setTimeout(resolve, 0));
    const after = transport.sent.filter((s) => s.type === 'control').length;
    expect(after).toBe(before); // no grant issued
    sync.unbindSession();
  });

  it('denies a control request even when permission exists until an approval UI is available', async () => {
    const { transport, sync } = setup({ perms: { ...DEFAULT_PERMISSIONS, remote_control: true } });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const before = transport.sentView.length;
    transport.emitControl({
      sessionId: 'sess-test', senderUserId: 'peer', kind: 'request', grantToken: null, createdAt: Date.now(),
    }, 'sess-test');
    await new Promise((resolve) => setTimeout(resolve, 0));
    const after = transport.sentView.length;
    expect(after).toBe(before);
    expect(sync.hasControlGrant()).toBe(false);
    sync.unbindSession();
  });

  it('revoke clears an existing grant', async () => {
    const { transport, sync } = setup({ perms: { ...DEFAULT_PERMISSIONS, remote_control: true } });
    transport.emitControl({
      sessionId: 'sess-test', senderUserId: 'peer', kind: 'revoke', grantToken: null, createdAt: Date.now(),
    }, 'sess-test');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(sync.hasControlGrant()).toBe(false);
    sync.unbindSession();
  });

  it('denies control from a different session', async () => {
    const { transport, sync } = setup({ perms: { ...DEFAULT_PERMISSIONS, remote_control: true } });
    transport.emitControl({
      sessionId: 'sess-other', senderUserId: 'peer', kind: 'request', grantToken: null, createdAt: Date.now(),
    }, 'sess-other');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(sync.hasControlGrant()).toBe(false);
    sync.unbindSession();
  });
});
