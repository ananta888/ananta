import { describe, expect, it } from 'vitest';
import {
  isActiveSurface,
  isAnnotation,
  isControlMessage,
  isCursorDelta,
  isCursorPos,
  isDeltaOp,
  isPermissionKey,
  isPermissionSet,
  isScrollPos,
  isScrollDelta,
  isSelectionPos,
  isSharedViewState,
  isViewStateDelta,
} from './pair-view-sync.validators';
import { DEFAULT_PERMISSIONS, PAIR_VIEW_SYNC_VERSION } from './pair-view-sync.types';

const basePerms = DEFAULT_PERMISSIONS;

describe('pair-view-sync.validators', () => {
  it('isPermissionKey accepts known keys', () => {
    for (const k of ['chat', 'view_tui', 'cursor', 'control', 'artifact_view', 'annotation']) {
      expect(isPermissionKey(k)).toBe(true);
    }
    expect(isPermissionKey('rogue_key')).toBe(false);
    expect(isPermissionKey(0)).toBe(false);
  });

  it('isPermissionSet rejects unknown keys and non-booleans', () => {
    expect(isPermissionSet({})).toBe(true);
    expect(isPermissionSet(basePerms)).toBe(true);
    expect(isPermissionSet({ chat: 'yes' })).toBe(false);
    expect(isPermissionSet({ rogue: true })).toBe(false);
    expect(isPermissionSet(null)).toBe(false);
  });

  it('isScrollPos requires finite x,y', () => {
    expect(isScrollPos({ x: 0, y: 0 })).toBe(true);
    expect(isScrollPos({ x: 1.5, y: -2.5 })).toBe(true);
    expect(isScrollPos({ x: NaN, y: 0 })).toBe(false);
    expect(isScrollPos({ x: 0 })).toBe(false);
  });

  it('isCursorPos allows nulls and non-negative ints', () => {
    expect(isCursorPos({ line: null, column: null })).toBe(true);
    expect(isCursorPos({ line: 0, column: 0 })).toBe(true);
    expect(isCursorPos({ line: -1, column: 0 })).toBe(false);
    expect(isCursorPos({ line: 0.5, column: 0 })).toBe(false);
  });

  it('isSelectionPos rejects start > end', () => {
    expect(isSelectionPos({ start: null, end: null })).toBe(true);
    expect(isSelectionPos({ start: 0, end: 10 })).toBe(true);
    expect(isSelectionPos({ start: 10, end: 0 })).toBe(false);
  });

  it('isActiveSurface accepts the documented union', () => {
    for (const s of ['chat', 'codecompass', 'artifact', 'terminal', 'settings', 'dashboard', 'pair', 'unknown']) {
      expect(isActiveSurface(s)).toBe(true);
    }
    expect(isActiveSurface('other')).toBe(false);
  });

  it('isDeltaOp requires whitelisted path and op type', () => {
    expect(isDeltaOp({ op: 'set', path: 'route', value: '/chat' })).toBe(true);
    expect(isDeltaOp({ op: 'unset', path: 'route' })).toBe(true);
    expect(isDeltaOp({ op: 'set', path: 'constructor', value: 1 })).toBe(false); // prototype guard
    expect(isDeltaOp({ op: 'foo', path: 'route' })).toBe(false);
  });

  it('isViewStateDelta caps ops at 64', () => {
    const many = Array.from({ length: 100 }, () => ({ op: 'set', path: 'route', value: '/' }));
    const x = {
      version: PAIR_VIEW_SYNC_VERSION,
      sessionId: 's', senderUserId: 'u', seq: 1, baseHash: 'h', newHash: 'h',
      kind: 'delta' as const, ops: many, createdAt: Date.now(),
    };
    expect(isViewStateDelta(x)).toBe(false);
  });

  it('isViewStateDelta accepts a snapshot envelope', () => {
    const x = {
      version: PAIR_VIEW_SYNC_VERSION,
      sessionId: 's', senderUserId: 'u', seq: 1, baseHash: 'h', newHash: 'h',
      kind: 'snapshot' as const, ops: [], createdAt: Date.now(),
    };
    expect(isViewStateDelta(x)).toBe(true);
  });

  it('isCursorDelta and isScrollDelta enforce the shape', () => {
    const okCursor = { sessionId: 's', senderUserId: 'u', cursor: { line: 0, column: 0 }, createdAt: 1 };
    expect(isCursorDelta(okCursor)).toBe(true);
    expect(isCursorDelta({ ...okCursor, cursor: null })).toBe(false);

    const okScroll = { sessionId: 's', senderUserId: 'u', scroll: { x: 0, y: 0 }, createdAt: 1 };
    expect(isScrollDelta(okScroll)).toBe(true);
  });

  it('isControlMessage enforces control semantics', () => {
    const base = { sessionId: 's', senderUserId: 'u', createdAt: 1 };
    expect(isControlMessage({ ...base, kind: 'request', grantToken: null })).toBe(true);
    expect(isControlMessage({ ...base, kind: 'grant', grantToken: 'tok' })).toBe(true);
    expect(isControlMessage({ ...base, kind: 'grant', grantToken: '' })).toBe(false);
    expect(isControlMessage({ ...base, kind: 'request', grantToken: 'x' })).toBe(true);
    expect(isControlMessage({ ...base, kind: 'foo', grantToken: null })).toBe(false);
  });

  it('isAnnotation caps body at 4 KB', () => {
    const base = { sessionId: 's', senderUserId: 'u', targetPath: '/x', createdAt: 1, body: 'hello' };
    expect(isAnnotation(base)).toBe(true);
    const big = { ...base, body: 'x'.repeat(4097) };
    expect(isAnnotation(big)).toBe(false);
  });

  it('isSharedViewState rejects out-of-range scalars', () => {
    const ok = {
      version: PAIR_VIEW_SYNC_VERSION, sessionId: 's', ownerUserId: 'u', seq: 0,
      route: '/chat', queryParams: {},
      activeSurface: 'chat', activeTab: 't', activePanel: 'p',
      activeArtifactId: null, activeArtifactHash: null, activeFilePath: null, activeSymbolId: null,
      scroll: { x: 0, y: 0 }, cursor: { line: null, column: null }, selection: { start: null, end: null },
      zoom: null, collapsedSections: [], viewHash: 'hh', createdAt: 1,
    };
    expect(isSharedViewState(ok)).toBe(true);
    const tooLong = { ...ok, route: '/' + 'a'.repeat(2049) };
    expect(isSharedViewState(tooLong)).toBe(false);
  });
});
