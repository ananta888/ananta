import { BehaviorSubject, Observable } from 'rxjs';

import { hasPermission } from './permission-labels';
import type { PermissionSet, RemoteViewProjection, SharedViewState } from './pair-view-sync.types';
import { isViewStateDelta } from './pair-view-sync.validators';
import { deltaSetsArtifactReference, emptyRemoteViewBase, redactRemoteArtifacts } from './pair-view-sync-projection';
import type { ViewDeltaService } from './view-delta.service';

/**
 * Result of applying one authenticated view payload:
 * - `rejected`: malformed, foreign, forbidden, stale or hash-mismatched;
 * - `snapshot_required`: a delta without a usable base (the caller requests
 *   a snapshot and counts the payload as rejected);
 * - `accepted` / `accepted_snapshot`: the read model was published.
 */
export type RemoteViewApplyOutcome = 'rejected' | 'snapshot_required' | 'accepted' | 'accepted_snapshot';

/**
 * Per-peer remote view read model of the pair view sync (T07). Each
 * authenticated sender gets its own monotonic projection; the local
 * SharedViewState is never mutated by remote payloads.
 */
export class PairRemoteViewProjections {
  private readonly views$ = new BehaviorSubject<ReadonlyMap<string, RemoteViewProjection>>(new Map());
  readonly remoteViews$: Observable<ReadonlyMap<string, RemoteViewProjection>> = this.views$.asObservable();

  constructor(
    private readonly deltaEngine: Pick<ViewDeltaService, 'applyDelta' | 'requiresSnapshotRequest'>,
    private readonly hashOf: (state: SharedViewState) => string,
  ) {}

  /** Publishes an empty read model. */
  clear(): void {
    this.views$.next(new Map());
  }

  /** Drops artifact references from every projection once artifact_share is gone. */
  redactForbiddenArtifacts(permissions: PermissionSet | null): void {
    if (hasPermission(permissions, 'artifact_share') || this.views$.value.size === 0) return;
    this.views$.next(redactRemoteArtifacts(this.views$.value));
  }

  applyAuthenticated(
    plain: string,
    authenticatedSenderId: string,
    sessionId: string,
    permissions: () => PermissionSet | null,
  ): RemoteViewApplyOutcome {
    let parsed: unknown;
    try { parsed = JSON.parse(plain); } catch { return 'rejected'; }
    if (!isViewStateDelta(parsed)) return 'rejected';
    const delta = parsed;
    if (delta.sessionId !== sessionId || delta.senderUserId !== authenticatedSenderId) return 'rejected';
    const perms = permissions();
    if (!hasPermission(perms, 'view_tui')) return 'rejected';
    if (!hasPermission(perms, 'artifact_share') && deltaSetsArtifactReference(delta)) return 'rejected';
    const currentProjection = this.views$.value.get(authenticatedSenderId)?.state ?? null;
    if (currentProjection && delta.seq <= currentProjection.seq) {
      // AEAD/replay validation authenticates each envelope but deliberately
      // permits a bounded out-of-order network window. The read model itself
      // is monotonic per authenticated sender, including full snapshots.
      return 'rejected';
    }
    if (
      delta.kind !== 'snapshot'
      && (!currentProjection || this.deltaEngine.requiresSnapshotRequest(delta, currentProjection))
    ) return 'snapshot_required';
    const base = currentProjection ?? emptyRemoteViewBase(delta, sessionId, authenticatedSenderId);
    const next = this.deltaEngine.applyDelta(base, delta);
    if (this.hashOf(next) !== delta.newHash) return 'rejected';
    const projections = new Map(this.views$.value);
    projections.set(authenticatedSenderId, Object.freeze({
      senderUserId: authenticatedSenderId,
      state: Object.freeze({ ...next }),
      receivedAt: Date.now(),
    }));
    this.views$.next(projections);
    return delta.kind === 'snapshot' ? 'accepted_snapshot' : 'accepted';
  }
}
