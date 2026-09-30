import { Subscription } from 'rxjs';

import type { CodeCompassGraphInventoryPage } from '../services/internals.service';

/**
 * Load strategies, preview bounds and the small stateful helpers of the
 * CodeHug wiki graph: cursor validation for domain inventory paging and a
 * replaceable async operation slot with a stale-response boundary.
 */

export type GraphLoadStrategyId = 'fast' | 'balanced' | 'detail';

export interface GraphLoadStrategy {
  readonly id: GraphLoadStrategyId;
  readonly label: string;
  readonly initialNodes: number;
  readonly stepNodes: number;
}

export const GRAPH_LOAD_STRATEGIES: readonly GraphLoadStrategy[] = Object.freeze([
  { id: 'fast', label: 'Schnellstart · 100', initialNodes: 100, stepNodes: 100 },
  { id: 'balanced', label: 'Ausgewogen · 250', initialNodes: 250, stepNodes: 125 },
  { id: 'detail', label: 'Detailfenster · 500', initialNodes: 500, stepNodes: 0 },
]);

// These bound only the optional topology preview. Complete staged loads page
// through the entire selected scope and do not use either value as a total cap.
export const MAX_GRAPH_PREVIEW_NODES = 500;
export const MAX_GRAPH_PREVIEW_EDGES = 2_000;

export type FullGraphLoadState = 'idle' | 'nodes' | 'edges' | 'complete' | 'cancelled' | 'error';

export type GraphDomainCursorInvalidReason =
  | 'cursor_repeated'
  | 'cursor_without_progress'
  | 'cursor_after_total'
  | 'terminal_before_total'
  | 'loaded_exceeds_total';

export type GraphDomainCursorDecision =
  | { readonly kind: 'complete' }
  | { readonly kind: 'next'; readonly cursor: string }
  | {
      readonly kind: 'invalid';
      readonly reason: GraphDomainCursorInvalidReason;
    };

/** Validates cursor progress independently from component and transport state. */
export class GraphDomainInventoryCursorGuard {
  private readonly requestedCursors = new Set<string>();
  private previousLoadedCount = 0;

  decide(
    page: CodeCompassGraphInventoryPage,
    loadedCount: number,
  ): GraphDomainCursorDecision {
    if (loadedCount > page.totalDomains) {
      return { kind: 'invalid', reason: 'loaded_exceeds_total' };
    }
    if (page.nextCursor === null) {
      return loadedCount === page.totalDomains
        ? { kind: 'complete' }
        : { kind: 'invalid', reason: 'terminal_before_total' };
    }
    if (this.requestedCursors.has(page.nextCursor)) {
      return { kind: 'invalid', reason: 'cursor_repeated' };
    }
    if (loadedCount <= this.previousLoadedCount) {
      return { kind: 'invalid', reason: 'cursor_without_progress' };
    }
    if (loadedCount >= page.totalDomains) {
      return { kind: 'invalid', reason: 'cursor_after_total' };
    }
    this.previousLoadedCount = loadedCount;
    this.requestedCursors.add(page.nextCursor);
    return { kind: 'next', cursor: page.nextCursor };
  }
}

/** One replaceable async operation with an explicit stale-response boundary. */
export class WikiOperationSlot {
  private generation = 0;
  private request: Subscription | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;

  restart(): number {
    this.cancel();
    return this.generation;
  }

  isCurrent(generation: number): boolean {
    return generation === this.generation;
  }

  replaceRequest(generation: number, request: Subscription): void {
    if (!this.isCurrent(generation)) {
      request.unsubscribe();
      return;
    }
    this.request?.unsubscribe();
    this.request = request;
  }

  schedule(generation: number, callback: () => void, delayMs: number): void {
    if (!this.isCurrent(generation)) return;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      if (this.isCurrent(generation)) callback();
    }, delayMs);
  }

  cancel(): void {
    this.generation += 1;
    this.request?.unsubscribe();
    this.request = null;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
}
