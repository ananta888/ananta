import { describe, expect, it, beforeEach } from 'vitest';
import { SharedViewStateService } from './shared-view-state.service';
import { ViewDeltaService } from './view-delta.service';
import { AppShellStateService } from './app-shell-state.service';
import {
  PAIR_VIEW_SYNC_VERSION,
  ScrollPos,
  SharedViewState,
} from './pair-view-sync.types';
import { TestBed } from '@angular/core/testing';
import { Router, ActivatedRoute } from '@angular/router';
import { provideRouter } from '@angular/router';

function emptyState(): SharedViewState {
  return {
    version: PAIR_VIEW_SYNC_VERSION,
    sessionId: 'sess-1',
    ownerUserId: 'owner-1',
    seq: 0,
    route: '/chat',
    queryParams: {},
    activeSurface: 'chat',
    activeTab: '',
    activePanel: '',
    activeArtifactId: null,
    activeArtifactHash: null,
    activeFilePath: null,
    activeSymbolId: null,
    scroll: { x: 0, y: 0 },
    cursor: { line: null, column: null },
    selection: { start: null, end: null },
    zoom: null,
    collapsedSections: [],
    viewHash: '00000000',
    createdAt: 100,
  };
}

describe('SharedViewStateService', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter([])],
    });
  });

  it('captures initial state with stable hash', () => {
    const svc = TestBed.runInInjectionContext(() => new SharedViewStateService());
    const state = svc.current;
    expect(state.version).toBe(PAIR_VIEW_SYNC_VERSION);
    expect(state.viewHash).toMatch(/^[0-9a-f]{8}$/);
    // Hash should be stable across reads of an unchanged state
    const a = svc.current.viewHash;
    const b = svc.current.viewHash;
    expect(a).toBe(b);
  });

  it('changes hash when a sync field changes', () => {
    const svc = TestBed.runInInjectionContext(() => new SharedViewStateService());
    const before = svc.current.viewHash;
    svc.updatePartial({ route: '/codecompass' });
    const after = svc.current.viewHash;
    expect(after).not.toBe(before);
  });

  it('binds to a session and stamps owner/session on state', () => {
    const svc = TestBed.runInInjectionContext(() => new SharedViewStateService());
    svc.bindToSession('sess-1', 'owner-1');
    const state = svc.current;
    expect(state.sessionId).toBe('sess-1');
    expect(state.ownerUserId).toBe('owner-1');
  });

  it('updateScroll is a no-op when value is unchanged', () => {
    const svc = TestBed.runInInjectionContext(() => new SharedViewStateService());
    const seq0 = svc.current.seq;
    svc.updateScroll({ x: 0, y: 0 });
    expect(svc.current.seq).toBe(seq0);
  });

  it('hashOf is stable for the same input', () => {
    const svc = TestBed.runInInjectionContext(() => new SharedViewStateService());
    const a = svc.hashOf(emptyState());
    const b = svc.hashOf(emptyState());
    expect(a).toBe(b);
  });
});

describe('ViewDeltaService', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
  });

  it('classifies a scroll-only change as kind=scroll', () => {
    const view = TestBed.runInInjectionContext(() => new SharedViewStateService());
    const delta = TestBed.runInInjectionContext(() => new ViewDeltaService(view));
    const a: SharedViewState = { ...emptyState(), scroll: { x: 0, y: 0 }, viewHash: 'aaa' };
    const b: SharedViewState = { ...a, seq: a.seq + 1, scroll: { x: 0, y: 200 }, viewHash: 'bbb', createdAt: a.createdAt + 1 };
    const d = delta.createDelta(a, b);
    expect(d.kind).toBe('scroll');
  });

  it('classifies a cursor-only change as kind=cursor', () => {
    const view = TestBed.runInInjectionContext(() => new SharedViewStateService());
    const delta = TestBed.runInInjectionContext(() => new ViewDeltaService(view));
    const a: SharedViewState = { ...emptyState(), cursor: { line: 1, column: 0 }, viewHash: 'aaa' };
    const b: SharedViewState = { ...a, seq: a.seq + 1, cursor: { line: 2, column: 0 }, viewHash: 'bbb', createdAt: a.createdAt + 1 };
    const d = delta.createDelta(a, b);
    expect(d.kind).toBe('cursor');
  });

  it('classifies a route/tab change as kind=delta with op set', () => {
    const view = TestBed.runInInjectionContext(() => new SharedViewStateService());
    const delta = TestBed.runInInjectionContext(() => new ViewDeltaService(view));
    const a: SharedViewState = { ...emptyState(), viewHash: 'aaa' };
    const b: SharedViewState = { ...a, seq: a.seq + 1, activeTab: 'logs', viewHash: 'bbb', createdAt: a.createdAt + 1 };
    const d = delta.createDelta(a, b);
    expect(d.kind).toBe('delta');
    expect(d.ops.length).toBeGreaterThan(0);
    expect(d.ops.some((o) => o.path === 'activeTab')).toBe(true);
  });

  it('applyDelta is pure: does not mutate the base state', () => {
    const view = TestBed.runInInjectionContext(() => new SharedViewStateService());
    const delta = TestBed.runInInjectionContext(() => new ViewDeltaService(view));
    const a: SharedViewState = { ...emptyState(), viewHash: 'aaa' };
    const b: SharedViewState = { ...a, seq: a.seq + 1, activeTab: 'logs', viewHash: 'bbb', createdAt: a.createdAt + 1 };
    const d = delta.createDelta(a, b);
    const next = delta.applyDelta(a, d);
    expect(next).not.toBe(a);
    expect(next.activeTab).toBe('logs');
    expect(a.activeTab).toBe('');
  });

  it('requiresSnapshotRequest is true on baseHash mismatch', () => {
    const view = TestBed.runInInjectionContext(() => new SharedViewStateService());
    const delta = TestBed.runInInjectionContext(() => new ViewDeltaService(view));
    const a: SharedViewState = { ...emptyState(), viewHash: 'aaa' };
    const b: SharedViewState = { ...a, seq: a.seq + 1, activeTab: 'logs', viewHash: 'bbb', createdAt: a.createdAt + 1 };
    const d = delta.createDelta(a, b);
    const local: SharedViewState = { ...a, viewHash: 'xxx' };
    expect(delta.requiresSnapshotRequest(d, local)).toBe(true);
  });

  it('createSnapshot carries no ops and is a snapshot', () => {
    const view = TestBed.runInInjectionContext(() => new SharedViewStateService());
    const delta = TestBed.runInInjectionContext(() => new ViewDeltaService(view));
    const s = emptyState();
    const d = delta.createSnapshot(s);
    expect(d.kind).toBe('snapshot');
    expect(d.ops).toEqual([]);
    expect(d.baseHash).toBe(s.viewHash);
    expect(d.newHash).toBe(s.viewHash);
  });
});
