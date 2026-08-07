import { Injectable, inject } from '@angular/core';

import {
  GraphArtifactIssue,
  GraphEvidenceClassification,
  GraphProjectionIssue,
  GraphSemanticIssue,
  GraphViewportIssue,
  GraphViewportSummary,
} from '../models/graph-viewport-summary.model';
import {
  GenericGraphModel,
  GraphSemanticScopeEvidence,
  GraphSemanticTranslationEvidence,
} from '../models/graph.model';
import { GraphColorService } from './graph-color.service';

const COMPLETE_EVIDENCE_STATES = new Set(['available', 'complete', 'ready', 'verified']);

function issue<TCode extends string>(
  code: TCode,
  affectedCount: number | null = null,
  reasonCode: string | null = null,
): Readonly<GraphViewportIssue<TCode>> {
  return Object.freeze({ code, affectedCount, reasonCode });
}

function freezeIssues<TIssue extends GraphViewportIssue<string>>(
  issues: TIssue[],
): readonly Readonly<TIssue>[] {
  return Object.freeze(issues.map(item => Object.freeze({ ...item })));
}

@Injectable({ providedIn: 'root' })
export class GraphViewportSummaryService {
  private readonly colors = inject(GraphColorService);

  project(
    graph: GenericGraphModel,
    visibleNodeIds: ReadonlySet<string>,
    visibleEdgeIds: ReadonlySet<string>,
  ): Readonly<GraphViewportSummary> {
    const loadedDomains = new Set<string>();
    const visibleDomains = new Set<string>();
    for (const node of graph.nodes) {
      const domainId = this.colors.resolveCanonicalDomain(node).canonicalId;
      loadedDomains.add(domainId);
      if (visibleNodeIds.has(node.id)) visibleDomains.add(domainId);
    }

    const loadedRelations = new Set<string>();
    const visibleRelations = new Set<string>();
    for (const edge of graph.edges) {
      const relation = edge.rawEdgeType ?? edge.edgeType;
      loadedRelations.add(relation);
      if (visibleEdgeIds.has(edge.id)) visibleRelations.add(relation);
    }

    const loadedNodes = graph.nodes.length;
    const loadedEdges = graph.edges.length;
    const visibleNodes = graph.nodes.filter(node => visibleNodeIds.has(node.id)).length;
    const visibleEdges = graph.edges.filter(edge => visibleEdgeIds.has(edge.id)).length;
    const window = graph.evidence?.window ?? null;
    const rawScopeTotalNodes = window?.scopeTotalNodes ?? window?.totalNodes ?? null;
    const rawGlobalTotalNodes = window?.globalTotalNodes ?? rawScopeTotalNodes;
    const rawScopeTotalEdges = window?.totalEdges ?? null;
    const rawGlobalTotalEdges = window?.globalTotalEdges ?? rawScopeTotalEdges;
    const rawInternalEdges = window?.internalEdges ?? null;
    const rawScopeUnresolvedEdges = window?.scopeUnresolvedEdges ?? null;
    const completeStagedScope = window?.view === 'staged'
      && window.deliveryComplete === true;
    const stagedScopeEdgeCompositionMatches = completeStagedScope
      && rawInternalEdges !== null
      && rawScopeUnresolvedEdges !== null
      && loadedEdges === rawInternalEdges + rawScopeUnresolvedEdges;

    const nodeEvidenceInconsistent = (
      (rawScopeTotalNodes !== null && rawScopeTotalNodes < loadedNodes)
      || (
        rawScopeTotalNodes !== null
        && rawGlobalTotalNodes !== null
        && rawGlobalTotalNodes < rawScopeTotalNodes
      )
    );
    const internalEdgeEvidenceInconsistent = rawInternalEdges !== null
      && rawInternalEdges < loadedEdges
      && !stagedScopeEdgeCompositionMatches;
    const edgeEvidenceInconsistent = (
      internalEdgeEvidenceInconsistent
      || (rawScopeTotalEdges !== null && rawScopeTotalEdges < loadedEdges)
      || (
        rawScopeTotalEdges !== null
        && rawInternalEdges !== null
        && rawScopeTotalEdges < rawInternalEdges
      )
      || (
        rawScopeTotalEdges !== null
        && rawGlobalTotalEdges !== null
        && rawGlobalTotalEdges < rawScopeTotalEdges
      )
    );
    const scopeTotalNodes = rawScopeTotalNodes === null || nodeEvidenceInconsistent
      ? null
      : rawScopeTotalNodes;
    const totalNodes = rawGlobalTotalNodes === null || nodeEvidenceInconsistent
      ? null
      : rawGlobalTotalNodes;
    const internalEdges = rawInternalEdges === null || internalEdgeEvidenceInconsistent
      ? null
      : rawInternalEdges;
    const totalEdges = rawGlobalTotalEdges === null || edgeEvidenceInconsistent
      ? null
      : rawGlobalTotalEdges;
    const nodeWindowBounded = rawScopeTotalNodes === null || nodeEvidenceInconsistent
      ? null
      : rawScopeTotalNodes > loadedNodes;

    const countDerivedEdgeCap = rawInternalEdges === null || rawInternalEdges < loadedEdges
      ? null
      : rawInternalEdges > loadedEdges;
    const explicitEdgeCap = window?.edgeCapped ?? null;
    const edgeCapEvidenceInconsistent = explicitEdgeCap !== null
      && countDerivedEdgeCap !== null
      && explicitEdgeCap !== countDerivedEdgeCap;
    const edgeWindowCapped = internalEdgeEvidenceInconsistent
      ? null
      : explicitEdgeCap === true || countDerivedEdgeCap === true
        ? true
        : edgeCapEvidenceInconsistent
          ? null
          : explicitEdgeCap ?? countDerivedEdgeCap;

    const projectionIssues: GraphProjectionIssue[] = [];
    if (nodeEvidenceInconsistent || edgeEvidenceInconsistent || edgeCapEvidenceInconsistent) {
      projectionIssues.push(issue('graph_window_evidence_inconsistent'));
    }
    if (nodeWindowBounded) {
      projectionIssues.push(issue(
        'graph_node_window_bounded',
        scopeTotalNodes === null ? null : scopeTotalNodes - loadedNodes,
      ));
    }
    if (edgeWindowCapped) {
      projectionIssues.push(issue(
        'graph_edge_window_capped',
        internalEdges === null ? null : Math.max(0, internalEdges - loadedEdges),
      ));
    }
    if ((window?.unresolvedEdges ?? 0) > 0) {
      projectionIssues.push(issue(
        'graph_relations_unresolved',
        window!.unresolvedEdges,
      ));
    }

    const semantic = this.semanticSummary(
      graph.evidence?.semanticTranslation ?? null,
      graph.evidence?.semanticScope ?? null,
      completeStagedScope,
    );
    const artifact = this.artifactSummary(graph);
    return Object.freeze({
      nodes: Object.freeze({ visible: visibleNodes, loaded: loadedNodes, total: totalNodes }),
      edges: Object.freeze({
        visible: visibleEdges,
        loaded: loadedEdges,
        internalWindow: internalEdges,
        total: totalEdges,
      }),
      scopeNodeTotal: scopeTotalNodes,
      scopeBoundaryEdges: window?.scopeBoundaryEdges ?? null,
      scopeUnresolvedEdges: rawScopeUnresolvedEdges,
      domains: Object.freeze({ visible: visibleDomains.size, loaded: loadedDomains.size }),
      rawRelationTypes: Object.freeze({
        visible: visibleRelations.size,
        loaded: loadedRelations.size,
      }),
      nodeWindowBounded,
      edgeWindowCapped,
      semanticState: semantic.state,
      artifactState: artifact.state,
      projectionIssues: freezeIssues(projectionIssues),
      semanticIssues: semantic.issues,
      artifactIssues: artifact.issues,
      rawWarnings: Object.freeze([...graph.warnings]),
    });
  }

  private semanticSummary(
    semantic: Readonly<GraphSemanticTranslationEvidence> | null,
    scoped: Readonly<GraphSemanticScopeEvidence> | null,
    completeStagedScope: boolean,
  ): {
    readonly state: GraphEvidenceClassification;
    readonly issues: readonly Readonly<GraphSemanticIssue>[];
  } {
    if (scoped) return this.scopedSemanticSummary(scoped);
    if (completeStagedScope) {
      return {
        state: 'unknown',
        issues: freezeIssues([issue('semantic_scope_unverified')]),
      };
    }
    if (!semantic) return { state: 'unknown', issues: Object.freeze([]) };
    const status = semantic.status?.trim().toLowerCase() ?? '';
    const reasonCode = semantic.reasonCode;
    const budget = semantic.budget;
    const truncatedCounts = budget
      ? [
          budget.truncatedNodeCount,
          budget.truncatedEdgeCount,
        ]
      : [];
    const truncatedCandidateEdgeCount = budget?.truncatedCandidateEdgeCount ?? 0;
    const hasTruncatedCount = truncatedCounts.some(count => (count ?? 0) > 0)
      || truncatedCandidateEdgeCount > 0;
    const truncated = budget?.truncated === true || hasTruncatedCount;
    const unresolvedCount = budget?.unresolvedEdgeCount ?? 0;
    const unavailable = status === 'unavailable'
      || reasonCode === 'semantic_translation_index_unavailable';
    const diagnosticPartial = status === 'degraded'
      || reasonCode === 'semantic_graph_partial';
    const issues: GraphSemanticIssue[] = [];

    if (unavailable) {
      issues.push(issue('semantic_translation_unavailable', null, reasonCode));
    }
    if (truncated) {
      issues.push(issue(
        'semantic_graph_truncated',
        this.sumPositiveCounts(truncatedCounts),
        reasonCode,
      ));
    }
    if (truncatedCandidateEdgeCount > 0) {
      issues.push(issue(
        'semantic_graph_candidate_relations_truncated',
        truncatedCandidateEdgeCount,
        reasonCode,
      ));
    }
    if (unresolvedCount > 0) {
      issues.push(issue('semantic_graph_relations_unresolved', unresolvedCount, reasonCode));
    }
    if (diagnosticPartial && !unavailable && !truncated && unresolvedCount === 0) {
      issues.push(issue('semantic_graph_degraded', null, reasonCode));
    }

    const state: GraphEvidenceClassification = unavailable
      ? 'unavailable'
      : diagnosticPartial || truncated || unresolvedCount > 0
        ? 'partial'
        : COMPLETE_EVIDENCE_STATES.has(status)
          ? 'complete'
          : 'unknown';
    return { state, issues: freezeIssues(issues) };
  }

  private scopedSemanticSummary(
    scoped: Readonly<GraphSemanticScopeEvidence>,
  ): {
    readonly state: GraphEvidenceClassification;
    readonly issues: readonly Readonly<GraphSemanticIssue>[];
  } {
    const status = scoped.status?.trim().toLowerCase() ?? '';
    const completeProof = Boolean(
      scoped.scopeKey?.trim()
      && scoped.supplementDomainKeys
      && scoped.supplementDomainKeys.length > 0
      && scoped.sourceRevisionId?.trim()
      && scoped.sourceRevisionDigest?.trim()
      && scoped.graphRevision?.trim()
      && scoped.supplementNodeCount !== null
      && scoped.supplementEdgeCount !== null
      && scoped.supplementDeclarationCount !== null,
    );
    if (scoped.complete === true && status === 'complete' && completeProof) {
      return { state: 'complete', issues: Object.freeze([]) };
    }
    if (status === 'unavailable' && scoped.complete === false) {
      return {
        state: 'unavailable',
        issues: freezeIssues([
          issue('semantic_scope_unavailable', null, 'semantic_scope_unavailable'),
        ]),
      };
    }
    if (status === 'partial' && scoped.complete === false) {
      return {
        state: 'partial',
        issues: freezeIssues([
          issue('semantic_scope_partial', null, 'semantic_scope_partial'),
        ]),
      };
    }
    return {
      state: 'unknown',
      issues: freezeIssues([
        issue('semantic_scope_unverified', null, 'semantic_scope_evidence_invalid'),
      ]),
    };
  }

  private artifactSummary(graph: GenericGraphModel): {
    readonly state: GraphEvidenceClassification;
    readonly issues: readonly Readonly<GraphArtifactIssue>[];
  } {
    const artifact = graph.evidence?.artifactStatus ?? null;
    if (!artifact) return { state: 'unknown', issues: Object.freeze([]) };
    const state = artifact.state?.trim().toLowerCase() ?? '';
    if (state === 'unavailable') {
      return {
        state: 'unavailable',
        issues: freezeIssues([
          issue('graph_artifact_unavailable', null, artifact.reasonCode),
        ]),
      };
    }
    return {
      state: COMPLETE_EVIDENCE_STATES.has(state) ? 'complete' : 'unknown',
      issues: Object.freeze([]),
    };
  }

  private sumPositiveCounts(counts: readonly (number | null)[]): number | null {
    const known = counts.filter((count): count is number => count !== null);
    const sum = known.reduce((total, count) => total + count, 0);
    return sum > 0 ? sum : null;
  }
}
