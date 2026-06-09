/**
 * T04: ViewDeltaService — produces snapshots and minimal deltas.
 *
 * The contract is "small payload wins": for the four common
 * change kinds (route/tab, scroll, cursor, selection, artifact)
 * the service emits a lightweight, kind-tagged delta. Only
 * truly composite state changes (multiple fields at once, or
 * unknown) go through the full `ops` path.
 *
 * Hash contract: every state has a stable viewHash; a delta
 * carries baseHash (sender's current hash) and newHash (hash
 * of the candidate). The receiver checks baseHash against its
 * own current state; on mismatch it requests a fresh snapshot
 * instead of applying the delta.
 */
import { Injectable, inject } from '@angular/core';

import { SharedViewStateService } from './shared-view-state.service';
import {
  CursorPos,
  DeltaKind,
  DeltaOp,
  PAIR_VIEW_SYNC_VERSION,
  ScrollPos,
  SelectionPos,
  SharedViewState,
  ViewStateDelta,
} from './pair-view-sync.types';

const SCROLL_FIELDS: ReadonlyArray<keyof SharedViewState> = ['scroll'];
const CURSOR_FIELDS: ReadonlyArray<keyof SharedViewState> = ['cursor'];
const SELECTION_FIELDS: ReadonlyArray<keyof SharedViewState> = ['selection'];
const ARTIFACT_FIELDS: ReadonlyArray<keyof SharedViewState> = [
  'activeArtifactId',
  'activeArtifactHash',
  'activeFilePath',
  'activeSymbolId',
];
const ROUTE_FIELDS: ReadonlyArray<keyof SharedViewState> = [
  'route',
  'queryParams',
  'activeSurface',
  'activeTab',
  'activePanel',
];

@Injectable({ providedIn: 'root' })
export class ViewDeltaService {
  private view = inject(SharedViewStateService);
  constructor() {}

  /** Build a full snapshot envelope from a state. */
  createSnapshot(state: SharedViewState): ViewStateDelta {
    return {
      version: PAIR_VIEW_SYNC_VERSION,
      sessionId: state.sessionId,
      senderUserId: state.ownerUserId,
      seq: state.seq,
      baseHash: state.viewHash,
      newHash: state.viewHash,
      kind: 'snapshot',
      ops: [],
      createdAt: state.createdAt,
    };
  }

  /**
   * Build a minimal delta between two states. If the diff is
   * large, the delta is still returned (with the full op set) —
   * the receiver can then decide to drop it and request a fresh
   * snapshot via `requiresSnapshotRequest`.
   */
  createDelta(previous: SharedViewState, current: SharedViewState): ViewStateDelta {
    const kind = this.classify(previous, current);
    const ops = this.diffOps(previous, current);
    return {
      version: PAIR_VIEW_SYNC_VERSION,
      sessionId: current.sessionId,
      senderUserId: current.ownerUserId,
      seq: current.seq,
      baseHash: previous.viewHash,
      newHash: current.viewHash,
      kind,
      ops,
      createdAt: current.createdAt,
      payload: this.payloadFor(kind, current),
    };
  }

  /** True when only the listed fields changed since `previous`. */
  private classify(previous: SharedViewState, current: SharedViewState): DeltaKind {
    if (this.changedOnly(previous, current, SCROLL_FIELDS)) return 'scroll';
    if (this.changedOnly(previous, current, CURSOR_FIELDS)) return 'cursor';
    if (this.changedOnly(previous, current, SELECTION_FIELDS)) return 'selection';
    // Route/tab/artifact are all "composite" — they go through
    // 'delta' with a real op set.
    return 'delta';
  }

  private changedOnly(
    previous: SharedViewState,
    current: SharedViewState,
    fields: ReadonlyArray<keyof SharedViewState>,
  ): boolean {
    if (previous.viewHash === current.viewHash) return false;
    for (const f of fields) {
      if (!this.shallowEqual(previous[f], current[f])) return true;
    }
    // any other field changing disqualifies
    for (const f of Object.keys(previous) as ReadonlyArray<keyof SharedViewState>) {
      if (fields.includes(f)) continue;
      if (f === 'viewHash' || f === 'seq' || f === 'createdAt' || f === 'sessionId' || f === 'ownerUserId') continue;
      if (!this.shallowEqual(previous[f], current[f])) return false;
    }
    return true;
  }

  private shallowEqual(a: unknown, b: unknown): boolean {
    if (a === b) return true;
    if (typeof a !== typeof b) return false;
    if (a === null || b === null) return a === b;
    if (typeof a !== 'object') return false;
    if (Array.isArray(a) && Array.isArray(b)) {
      if (a.length !== b.length) return false;
      for (let i = 0; i < a.length; i++) {
        if (!this.shallowEqual(a[i], b[i])) return false;
      }
      return true;
    }
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    const ao = a as Record<string, unknown>;
    const bo = b as Record<string, unknown>;
    const akeys = Object.keys(ao);
    const bkeys = Object.keys(bo);
    if (akeys.length !== bkeys.length) return false;
    for (const k of akeys) {
      if (!this.shallowEqual(ao[k], bo[k])) return false;
    }
    return true;
  }

  private diffOps(previous: SharedViewState, current: SharedViewState): readonly DeltaOp[] {
    const ops: DeltaOp[] = [];
    const fields: ReadonlyArray<keyof SharedViewState> = [
      'route', 'queryParams', 'activeSurface', 'activeTab', 'activePanel',
      'activeArtifactId', 'activeArtifactHash', 'activeFilePath', 'activeSymbolId',
      'scroll', 'cursor', 'selection', 'zoom', 'collapsedSections',
    ];
    for (const f of fields) {
      if (this.shallowEqual(previous[f], current[f])) continue;
      if (current[f] === null) {
        ops.push({ op: 'unset', path: f });
      } else {
        ops.push({ op: 'set', path: f, value: current[f] });
      }
    }
    return ops;
  }

  private payloadFor(kind: DeltaKind, current: SharedViewState): CursorPos | SelectionPos | ScrollPos | null {
    if (kind === 'cursor') return current.cursor;
    if (kind === 'selection') return current.selection;
    if (kind === 'scroll') return current.scroll;
    return null;
  }

  /**
   * Apply a delta to a base state. Used by the receiver to
   * compute the candidate state. The function is pure; the
   * caller decides whether to commit.
   */
  applyDelta(base: SharedViewState, delta: ViewStateDelta): SharedViewState {
    if (delta.kind === 'snapshot' || (delta.kind === 'delta' && delta.ops.length === 0)) {
      return { ...base, viewHash: delta.newHash, seq: delta.seq, createdAt: delta.createdAt };
    }
    const next: SharedViewState = { ...base, seq: delta.seq, createdAt: delta.createdAt };
    for (const op of delta.ops) {
      // The path is whitelisted by the validators; here we
      // simply apply set/unset/append/remove mechanically.
      this.applyOp(next, op);
    }
    return { ...next, viewHash: delta.newHash };
  }

  private applyOp(state: SharedViewState, op: DeltaOp): void {
    // Only shallow top-level paths are supported. This is the
    // documented contract; deeper paths would require a path
    // parser and the complexity is not worth it for v1.
    const w = state as unknown as Record<string, unknown>;
    if (op.path === 'collapsedSections') {
      if (op.op === 'set' && Array.isArray(op.value)) {
        w['collapsedSections'] = [...op.value];
      }
      return;
    }
    if (op.op === 'set') {
      w[op.path] = op.value;
    } else if (op.op === 'unset') {
      w[op.path] = null;
    } else if (op.op === 'append' && op.path === 'collapsedSections') {
      const cur = state.collapsedSections;
      w['collapsedSections'] = [...cur, String(op.value)];
    } else if (op.op === 'remove' && op.path === 'collapsedSections') {
      const cur = state.collapsedSections;
      w['collapsedSections'] = cur.filter((s) => s !== op.value);
    }
  }

  /**
   * True when the receiver should request a fresh snapshot
   * because the delta's baseHash does not match the local
   * state's viewHash (e.g. messages were dropped or reordered).
   */
  requiresSnapshotRequest(delta: ViewStateDelta, local: SharedViewState): boolean {
    if (delta.kind === 'snapshot') return false;
    return delta.baseHash !== '' && delta.baseHash !== local.viewHash;
  }
}
