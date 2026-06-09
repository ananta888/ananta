/**
 * T02: SharedViewStateService — captures the user's current UI state.
 *
 * The service exposes a single `state$` Observable of
 * SharedViewState. Sources of updates:
 *
 *   - Router NavigationEnd events: route, queryParams, derived surface
 *   - Components calling `updatePartial(partial)`: artifact/file/symbol,
 *     scroll (debounced per surface), cursor, selection, collapsedSections
 *   - AppShellStateService for high-level mode/area (light signal bridge)
 *
 * Hash contract: the viewHash is computed from a stable
 * serialisation of the state (sorted keys, no whitespace) using
 * a small djb2-style hash. It is **stable across runs** (same
 * state -> same hash) and **sensitive to every sync field**.
 */
import { Injectable, inject } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { BehaviorSubject, Observable, filter } from 'rxjs';

import { AppShellStateService } from './app-shell-state.service';
import {
  ActiveSurface,
  PAIR_VIEW_SYNC_VERSION,
  PairViewUserContext,
  ScrollPos,
  SharedViewState,
} from './pair-view-sync.types';

const ROUTE_TO_SURFACE: ReadonlyArray<readonly [RegExp, ActiveSurface]> = [
  [/^\/chat(\/|$)/, 'chat'],
  [/^\/codecompass(\/|$)/, 'codecompass'],
  [/^\/artifacts?(\/|$)/, 'artifact'],
  [/^\/terminal(\/|$)/, 'terminal'],
  [/^\/settings(\/|$)/, 'settings'],
  [/^\/dashboard(\/|$)/, 'dashboard'],
  [/^\/pair(\/|$)/, 'pair'],
];

function routeToSurface(url: string): ActiveSurface {
  for (const [re, surface] of ROUTE_TO_SURFACE) {
    if (re.test(url)) return surface;
  }
  return 'unknown';
}

function stableStringify(value: unknown): string {
  // JSON.stringify with sorted keys; no whitespace; undefined -> omit.
  const seen = new WeakSet<object>();
  const walk = (v: unknown): unknown => {
    if (v === null || typeof v !== 'object') return v;
    if (seen.has(v as object)) return null; // cycle guard
    seen.add(v as object);
    if (Array.isArray(v)) return v.map(walk);
    const obj = v as Record<string, unknown>;
    const sorted: Record<string, unknown> = {};
    for (const k of Object.keys(obj).sort()) {
      const child = obj[k];
      if (child === undefined) continue;
      sorted[k] = walk(child);
    }
    return sorted;
  };
  return JSON.stringify(walk(value));
}

/** djb2 hash; deterministic and small. */
function djb2(str: string): string {
  let h = 5381;
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) + h + str.charCodeAt(i)) | 0;
  }
  // Hex, zero-padded to 8 chars; small but stable.
  return (h >>> 0).toString(16).padStart(8, '0');
}

function emptyScroll(): ScrollPos {
  return { x: 0, y: 0 };
}

function emptyState(): Omit<SharedViewState, 'viewHash' | 'createdAt' | 'ownerUserId' | 'sessionId' | 'seq'> {
  return {
    version: PAIR_VIEW_SYNC_VERSION,
    route: '/',
    queryParams: {},
    activeSurface: 'unknown',
    activeTab: '',
    activePanel: '',
    activeArtifactId: null,
    activeArtifactHash: null,
    activeFilePath: null,
    activeSymbolId: null,
    scroll: emptyScroll(),
    cursor: { line: null, column: null },
    selection: { start: null, end: null },
    zoom: null,
    collapsedSections: [],
  };
}

@Injectable({ providedIn: 'root' })
export class SharedViewStateService {
  private router = inject(Router);
  private activatedRoute = inject(ActivatedRoute);
  private shell = inject(AppShellStateService);

  private readonly userContext: PairViewUserContext = {
    sessionId: '',
    ownerUserId: '',
  };

  private readonly _state$ = new BehaviorSubject<SharedViewState>(this.buildInitial());
  readonly state$: Observable<SharedViewState> = this._state$.asObservable();

  /** Last-known state, useful for tests and for snapshot requests. */
  get current(): SharedViewState {
    return this._state$.value;
  }

  /** Bound to the current active share session. */
  bindToSession(sessionId: string, ownerUserId: string): void {
    this.userContext.sessionId = sessionId;
    this.userContext.ownerUserId = ownerUserId;
    this.recompute();
  }

  unbindFromSession(): void {
    this.userContext.sessionId = '';
    this.userContext.ownerUserId = '';
    this.recompute();
  }

  /**
   * Apply a partial update from a component. The service does
   * NOT debounce; callers should debounce scroll/cursor updates
   * themselves (see PairViewSyncService).
   */
  updatePartial(partial: Partial<SharedViewState>): void {
    const cur = this._state$.value;
    const next: SharedViewState = { ...cur, ...partial };
    this.publish(next);
  }

  /** Convenience: per-surface scroll position. */
  updateScroll(scroll: ScrollPos): void {
    const cur = this._state$.value;
    if (cur.scroll.x === scroll.x && cur.scroll.y === scroll.y) return;
    this.updatePartial({ scroll });
  }

  init(): void {
    this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe(() => this.onNavigationEnd());
    this.onNavigationEnd();
  }

  /** Compute viewHash from a candidate state without publishing it. */
  hashOf(state: Omit<SharedViewState, 'viewHash'>): string {
    return djb2(stableStringify(state));
  }

  private onNavigationEnd(): void {
    let current = this.activatedRoute.root;
    while (current.firstChild) current = current.firstChild;
    const url = this.router.url || '/';
    const params: Record<string, string> = {};
    for (const [k, v] of Object.entries(current.snapshot.queryParams)) {
      if (typeof v === 'string') params[k] = v;
    }
    const area = (current.snapshot.data['area'] as string | undefined) || '';
    const surface = routeToSurface(url);
    this.updatePartial({
      route: url,
      queryParams: params,
      activeSurface: surface,
      activePanel: area,
    });
  }

  private recompute(): void {
    const cur = this._state$.value;
    const next: SharedViewState = {
      ...cur,
      sessionId: this.userContext.sessionId,
      ownerUserId: this.userContext.ownerUserId,
    };
    this.publish(next);
  }

  private buildInitial(): SharedViewState {
    const skeleton = emptyState();
    return {
      ...skeleton,
      sessionId: '',
      ownerUserId: '',
      seq: 0,
      viewHash: this.hashOf({ ...skeleton, seq: 0 } as Omit<SharedViewState, 'viewHash'>),
      createdAt: Date.now(),
    };
  }

  private publish(next: SharedViewState): void {
    // Recompute the hash from the candidate state, EXCLUDING
    // the viewHash itself (which would obviously equal the
    // previous hash and short-circuit the change). The result
    // becomes the new viewHash; the sender's `newHash` is
    // verified by `requiresSnapshotRequest` before we get here.
    const { viewHash: _vh, ...rest } = next;
    const hash = this.hashOf(rest as Omit<SharedViewState, 'viewHash'>);
    if (hash === _vh && next.seq > 0) return;
    const out: SharedViewState = {
      ...next,
      viewHash: hash,
      seq: next.seq + 1,
      createdAt: Date.now(),
    };
    this._state$.next(out);
  }
}
