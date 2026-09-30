import {
  PAIR_VIEW_SYNC_VERSION,
  RemoteViewProjection,
  SharedViewState,
  ViewStateDelta,
} from './pair-view-sync.types';

/**
 * Pure view-state projections of the pair view sync: outgoing redaction,
 * remote read-model bases, artifact redaction and message identifiers.
 */

const ARTIFACT_REFERENCE_PATHS: readonly string[] = [
  'activeArtifactId', 'activeArtifactHash', 'activeFilePath', 'activeSymbolId',
];

export function newPairMessageId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function cloneViewState(state: SharedViewState): SharedViewState {
  return {
    ...state,
    queryParams: { ...state.queryParams },
    scroll: { ...state.scroll },
    cursor: { ...state.cursor },
    selection: { ...state.selection },
    collapsedSections: [...state.collapsedSections],
  };
}

/**
 * Local state as it may leave this device (without `viewHash`): bound to the
 * session/owner, query params dropped, artifact references only with
 * `artifact_share`, and pointer presence always stripped.
 */
export function projectOutgoingViewState(
  state: SharedViewState,
  sessionId: string,
  ownerUserId: string,
  shareArtifacts: boolean,
): SharedViewState {
  return {
    ...state,
    sessionId,
    ownerUserId,
    queryParams: {},
    activeArtifactId: shareArtifacts ? state.activeArtifactId : null,
    activeArtifactHash: shareArtifacts ? state.activeArtifactHash : null,
    activeFilePath: shareArtifacts ? state.activeFilePath : null,
    activeSymbolId: shareArtifacts ? state.activeSymbolId : null,
    scroll: { ...state.scroll },
    // Pointer/text-cursor presence has its own `remote_cursor` permission,
    // payload type and local consent. Never smuggle it through view_tui.
    cursor: { line: null, column: null },
    selection: { ...state.selection },
    collapsedSections: [...state.collapsedSections],
  };
}

/** True when a delta sets any artifact reference to a non-null value. */
export function deltaSetsArtifactReference(delta: ViewStateDelta): boolean {
  return delta.ops.some(op => ARTIFACT_REFERENCE_PATHS.includes(op.path) && op.op === 'set' && op.value !== null);
}

/** Copies remote projections with every artifact reference removed. */
export function redactRemoteArtifacts(
  views: ReadonlyMap<string, RemoteViewProjection>,
): Map<string, RemoteViewProjection> {
  const redacted = new Map<string, RemoteViewProjection>();
  for (const [senderId, projection] of views) {
    const state = {
      ...projection.state,
      activeArtifactId: null,
      activeArtifactHash: null,
      activeFilePath: null,
      activeSymbolId: null,
    };
    redacted.set(senderId, Object.freeze({ ...projection, state: Object.freeze(state) }));
  }
  return redacted;
}

/** Empty remote read model a first snapshot from `senderUserId` applies to. */
export function emptyRemoteViewBase(
  delta: ViewStateDelta,
  sessionId: string,
  senderUserId: string,
): SharedViewState {
  return {
    version: PAIR_VIEW_SYNC_VERSION,
    sessionId,
    ownerUserId: senderUserId,
    seq: 0,
    route: '/', queryParams: {}, activeSurface: 'unknown', activeTab: '', activePanel: '',
    activeArtifactId: null, activeArtifactHash: null, activeFilePath: null, activeSymbolId: null,
    scroll: { x: 0, y: 0 }, cursor: { line: null, column: null },
    selection: { start: null, end: null }, zoom: null, collapsedSections: [],
    viewHash: delta.baseHash, createdAt: 0,
  };
}
