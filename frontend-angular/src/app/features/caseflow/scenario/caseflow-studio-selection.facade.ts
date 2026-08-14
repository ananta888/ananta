import { Injectable, computed, signal } from '@angular/core';

import type { VpEdge, VpGraph } from '../../visual-process/visual-process-api.service';
import type { CaseFlowEdgeIdentity } from '../agent-canvas/caseflow-edge-trace.models';

export interface CaseFlowStudioNodeSelection {
  readonly kind: 'node';
  readonly graph_id: string;
  readonly step_id: string;
}

export interface CaseFlowStudioEdgeSelection {
  readonly kind: 'edge';
  readonly graph_id: string;
  readonly edge: CaseFlowEdgeIdentity;
  readonly reverse_edge: CaseFlowEdgeIdentity | null;
}

/**
 * Typed, workspace-local selection. A node and an edge may share an ID without
 * becoming ambiguous because their discriminant and edge endpoints are kept.
 */
export type CaseFlowStudioSelection =
  | CaseFlowStudioNodeSelection
  | CaseFlowStudioEdgeSelection
  | null;

/** Owns selection reconciliation only; graph editing remains in the editor facade. */
@Injectable()
export class CaseFlowStudioSelectionFacade {
  private readonly selectionState = signal<CaseFlowStudioSelection>(null);

  readonly selection = this.selectionState.asReadonly();
  readonly selectedNodeId = computed(() => {
    const selection = this.selectionState();
    return selection?.kind === 'node' ? selection.step_id : null;
  });
  readonly selectedEdge = computed(() => {
    const selection = this.selectionState();
    return selection?.kind === 'edge' ? selection.edge : null;
  });
  readonly reverseEdge = computed(() => {
    const selection = this.selectionState();
    return selection?.kind === 'edge' ? selection.reverse_edge : null;
  });

  selectNode(graph: Readonly<VpGraph>, stepId: string): void {
    const exactMatches = graph.steps.filter(step => step.id === stepId);
    this.selectionState.set(exactMatches.length === 1
      ? Object.freeze({
        kind: 'node',
        graph_id: graph.id,
        step_id: stepId,
      })
      : null);
  }

  selectEdge(graph: Readonly<VpGraph>, edge: Readonly<CaseFlowEdgeIdentity>): void {
    const selected = findExactEdge(graph.edges, edge);
    this.selectionState.set(selected
      ? edgeSelection(graph, selected)
      : null);
  }

  clear(): void {
    this.selectionState.set(null);
  }

  /** Clears graph drift fail-closed and refreshes only an exact unique reverse. */
  reconcileGraph(graph: Readonly<VpGraph>): void {
    const current = this.selectionState();
    if (!current) return;
    if (current.graph_id !== graph.id) {
      this.clear();
      return;
    }
    if (current.kind === 'node') {
      if (graph.steps.filter(step => step.id === current.step_id).length !== 1) this.clear();
      return;
    }
    const selected = findExactEdge(graph.edges, current.edge);
    if (!selected) {
      this.clear();
      return;
    }
    this.selectionState.set(edgeSelection(graph, selected));
  }
}

function edgeSelection(
  graph: Readonly<VpGraph>,
  selected: Readonly<VpEdge>,
): CaseFlowStudioEdgeSelection {
  const edge = edgeIdentity(selected);
  return Object.freeze({
    kind: 'edge',
    graph_id: graph.id,
    edge,
    reverse_edge: findUniqueReverse(graph.edges, edge),
  });
}

function findExactEdge(
  edges: readonly VpEdge[],
  identity: Readonly<CaseFlowEdgeIdentity>,
): VpEdge | null {
  const matches = edges.filter(edge => edge.id === identity.edge_id
    && edge.source === identity.source_step_id
    && edge.target === identity.target_step_id);
  return matches.length === 1 ? matches[0] : null;
}

function findUniqueReverse(
  edges: readonly VpEdge[],
  selected: Readonly<CaseFlowEdgeIdentity>,
): CaseFlowEdgeIdentity | null {
  if (selected.source_step_id === selected.target_step_id) return null;
  const matches = edges.filter(edge => edge.id !== selected.edge_id
    && edge.source === selected.target_step_id
    && edge.target === selected.source_step_id);
  return matches.length === 1 ? edgeIdentity(matches[0]) : null;
}

function edgeIdentity(edge: Readonly<VpEdge>): CaseFlowEdgeIdentity {
  return Object.freeze({
    edge_id: edge.id,
    source_step_id: edge.source,
    target_step_id: edge.target,
  });
}
