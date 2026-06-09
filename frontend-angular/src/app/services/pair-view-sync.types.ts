/**
 * T01 / T11: Shared types for Pair-Dev View-Sync.
 *
 * Single source of truth for the data contract between Owner and
 * Partner. The transport (WebRTC DataChannel vs Hub Relay) only
 * carries these envelopes; the application code never deals in
 * untyped JSON.
 *
 * Naming follows the existing backend keys exactly:
 *   chat, view_tui, cursor, control, artifact_view, annotation
 * (see share_sessions.py). UI labels may differ; the mapping
 * lives in permission-labels.ts.
 */

export const PAIR_VIEW_SYNC_VERSION = 1 as const;

export type PermissionKey =
  | 'chat'
  | 'view_tui'
  | 'cursor'
  | 'control'
  | 'artifact_view'
  | 'annotation';

export type PermissionSet = Readonly<Record<PermissionKey, boolean>>;

export const ALL_PERMISSIONS: readonly PermissionKey[] = [
  'chat',
  'view_tui',
  'cursor',
  'control',
  'artifact_view',
  'annotation',
] as const;

export const DEFAULT_PERMISSIONS: PermissionSet = Object.freeze({
  chat: true,
  view_tui: true,
  cursor: false,
  control: false,
  artifact_view: true,
  annotation: false,
});

/**
 * Internal-only context: which share session a SharedViewState
 * belongs to and which user owns it. Not transmitted on the
 * wire; the receiver derives both from its session context.
 */
export interface PairViewUserContext {
  sessionId: string;
  ownerUserId: string;
}

/**
 * The "main surface" the user is currently on. Used for cheap
 * routing-sync between Owner and Partner without needing DOM
 * introspection. Mapping comes from route-metadata (AppRouteArea).
 */
export type ActiveSurface =
  | 'chat'
  | 'codecompass'
  | 'artifact'
  | 'terminal'
  | 'settings'
  | 'dashboard'
  | 'pair'
  | 'unknown';

/** Stable position of the active tab inside its surface. */
export interface TabPosition {
  surface: ActiveSurface;
  tab: string;
  panel: string;
}

/** Reference to a specific artifact, file, or symbol. */
export interface ArtifactRef {
  artifactId: string | null;
  artifactHash: string | null;
  filePath: string | null;
  symbolId: string | null;
}

/**
 * Cursor position. Two flavours:
 *  - text-cursor: line + column (used by code/TUI panes)
 *  - pointer-cursor: x + y in viewport px (used by the mouse
 *    overlay; only set when the sender explicitly sends a
 *    pointer position, e.g. on pointermove)
 *
 * Receivers should treat both as independent; text-cursor and
 * pointer-cursor can coexist on the same SharedViewState.
 */
export interface CursorPos {
  line: number | null;
  column: number | null;
  x?: number;
  y?: number;
}

export interface SelectionPos {
  start: number | null;
  end: number | null;
}

export interface ScrollPos {
  x: number;
  y: number;
}

/**
 * The full view state. This is what `SharedViewStateService`
 * exposes via `state$`. Hash is computed from a stable
 * serialisation (sorted keys, no whitespace) so it is identical
 * for identical states.
 */
export interface SharedViewState {
  version: typeof PAIR_VIEW_SYNC_VERSION;
  sessionId: string;
  ownerUserId: string;
  seq: number;
  route: string;
  queryParams: Readonly<Record<string, string>>;
  activeSurface: ActiveSurface;
  activeTab: string;
  activePanel: string;
  activeArtifactId: string | null;
  activeArtifactHash: string | null;
  activeFilePath: string | null;
  activeSymbolId: string | null;
  scroll: ScrollPos;
  cursor: CursorPos;
  selection: SelectionPos;
  zoom: number | null;
  collapsedSections: readonly string[];
  viewHash: string;
  createdAt: number;
}

export type DeltaKind = 'snapshot' | 'delta' | 'cursor' | 'selection' | 'scroll' | 'control';

export type DeltaOp =
  | { op: 'set'; path: string; value: unknown }
  | { op: 'unset'; path: string }
  | { op: 'append'; path: string; value: unknown }
  | { op: 'remove'; path: string; value: unknown };

/**
 * A delta message. Either a `snapshot` (full state) or one of
 * the lightweight delta types. Lightweight deltas carry no `ops`
 * — the kind + the relevant primitive fields are enough.
 */
export interface ViewStateDelta {
  version: typeof PAIR_VIEW_SYNC_VERSION;
  sessionId: string;
  senderUserId: string;
  seq: number;
  baseHash: string;
  newHash: string;
  kind: DeltaKind;
  ops: readonly DeltaOp[];
  createdAt: number;
  /** Optional inline payload for lightweight deltas (cursor, scroll, etc). */
  payload?: CursorPos | SelectionPos | ScrollPos | null;
}

export interface CursorDelta {
  sessionId: string;
  senderUserId: string;
  cursor: CursorPos;
  createdAt: number;
}

export interface ScrollDelta {
  sessionId: string;
  senderUserId: string;
  scroll: ScrollPos;
  createdAt: number;
}

export type ControlKind = 'request' | 'grant' | 'revoke' | 'request_follow' | 'request_unfollow';

export interface ControlMessage {
  sessionId: string;
  senderUserId: string;
  kind: ControlKind;
  /** Grant is session-scoped; the token is opaque to the wire. */
  grantToken: string | null;
  createdAt: number;
}

/**
 * Envelope pushed to the Hub Relay. Backend expects exactly this
 * shape (see agent/routes/share_sessions.py push_view_payload).
 * The encrypted_payload field is opaque-by-contract: production
 * populates it with AES-GCM; test/dev profiles populate it with
 * a clearly-marked stub (see Permission-Default-Deny ADR).
 */
export interface RelayEnvelope {
  message_id: string;
  kind: DeltaKind;
  base_hash: string;
  new_hash: string;
  width: number;
  height: number;
  encrypted_payload: string;
}

export interface Annotation {
  sessionId: string;
  senderUserId: string;
  targetPath: string;
  /** Markdown, capped at 4 KB by the validators. */
  body: string;
  createdAt: number;
}
