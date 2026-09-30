import { CodeCompassFullGraphLoadError } from '../services/codecompass-full-graph-loader.service';
import type {
  CodeCompassGraphDomainFacet,
  CodeCompassSemanticScopeEvidence,
} from '../services/internals.service';
import type { FullGraphLoadState } from './codehug-wiki-graph.support';

/**
 * Pure mappers of the CodeHug wiki graph: user-facing error texts, graph
 * revision extraction from metadata and domain/count label helpers.
 */

export function fullGraphLoadErrorMessage(error: unknown): string {
  if (!(error instanceof CodeCompassFullGraphLoadError)) {
    return 'Der vollständige Graph konnte nicht vertragskonform geladen werden.';
  }
  if (
    error.reason === 'revision_changed'
    || error.reason === 'evidence_revision_changed'
  ) {
    return 'Der Index wurde während des vollständigen Ladens aktualisiert. Der alte Datenstrom wurde verworfen; bitte den aktuellen Indexstand laden und die Domain erneut auswählen.';
  }
  if (
    error.reason === 'scope_changed'
    || error.reason === 'semantic_scope_changed'
    || error.reason === 'source_changed'
  ) {
    return 'Der vollständige Datenstrom wurde wegen eines Source-/Scope-Wechsels verworfen.';
  }
  if (error.reason === 'duplicate_record') {
    return 'Der vollständige Datenstrom enthielt überlappende Knoten- oder Kanten-IDs und wurde ohne Teilübernahme verworfen.';
  }
  return 'Der vollständige Datenstrom wurde wegen inkonsistenter Seitencursor abgebrochen; es wurden keine Teildaten übernommen.';
}

export function codeGraphContentRevision(graph: unknown): string {
  if (!graph || typeof graph !== 'object' || Array.isArray(graph)) return '';
  const metadata = (graph as { metadata?: unknown }).metadata;
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) return '';
  const values = metadata as Record<string, unknown>;
  for (const field of [
    'content_graph_revision',
    'evidence_graph_revision',
    'parent_graph_revision',
    'graph_revision',
  ]) {
    const value = values[field];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

export function stableGraphEvidenceRevision(graph: unknown): string {
  if (!graph || typeof graph !== 'object' || Array.isArray(graph)) return '';
  const metadata = (graph as { metadata?: unknown }).metadata;
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) return '';
  const values = metadata as Record<string, unknown>;
  for (const field of ['evidence_graph_revision', 'parent_graph_revision']) {
    const value = values[field];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

export function graphDomainSourceLabel(source: string): string {
  switch (source) {
    case 'domain_id': return 'deklarierte Domain';
    case 'domain_path': return 'Domainpfad';
    case 'path': return 'Repositorypfad';
    case 'unassigned': return 'nicht zugeordnet';
    default: return source;
  }
}

export function metadataCountFrom(
  metadata: Record<string, unknown> | null | undefined,
  field: string,
): number | null {
  const value = metadata?.[field];
  return typeof value === 'number'
    && Number.isFinite(value)
    && Number.isInteger(value)
    && value >= 0
    ? value
    : null;
}

export function graphDomainOptionText(domain: CodeCompassGraphDomainFacet, includeSubdomains: boolean): string {
  const indentation = '— '.repeat(Math.min(Math.max(domain.depth, 0), 7));
  const count = includeSubdomains
    ? domain.subtreeNodeCount
    : domain.directNodeCount;
  const fullPath = domain.source === 'unassigned'
    ? domain.label
    : domain.path;
  const semanticCount = includeSubdomains
    && domain.semanticScopeStatus === 'available'
    ? domain.semanticNodeCount
    : undefined;
  const countLabel = semanticCount === undefined
    ? count.toLocaleString('de-DE')
    : `${(domain.baseNodeCount ?? count).toLocaleString('de-DE')} Struktur + ${semanticCount.toLocaleString('de-DE')} Symbole`;
  return `${indentation}${fullPath} · ${graphDomainSourceLabel(domain.source)} (${countLabel})`;
}

export function semanticScopeToolbarText(complete: boolean, status: string): string {
  if (complete) return 'Domain vollständig geladen';
  switch (status) {
    case 'unavailable': return 'Transport vollständig · Semantik nicht verfügbar';
    case 'partial': return 'Transport vollständig · Semantik unvollständig';
    default: return 'Transport vollständig · Semantik nicht verifiziert';
  }
}

export interface FullGraphProgressSnapshot {
  readonly state: FullGraphLoadState;
  readonly loadedNodes: number;
  readonly totalNodes: number;
  readonly loadedEdges: number;
  readonly totalEdges: number;
  readonly domainSelected: boolean;
}

export function fullGraphProgressText(progress: FullGraphProgressSnapshot): string {
  const { state, loadedNodes, totalNodes, loadedEdges, totalEdges } = progress;
  if (state === 'nodes') {
    return `Scope-Transport: Knoten ${loadedNodes} / ${totalNodes || '…'}`;
  }
  if (state === 'edges') {
    return `Scope-Transport: Knoten ${loadedNodes} / ${totalNodes} · Kanten ${loadedEdges} / ${totalEdges || '…'}`;
  }
  if (state === 'complete') {
    const label = progress.domainSelected ? 'Domain-Scope' : 'Basisindex';
    return `${label} vollständig übertragen: ${loadedNodes} Knoten · ${loadedEdges} Kanten`;
  }
  if (state === 'cancelled') return 'Scope-Transport abgebrochen';
  return '';
}

export function graphDomainInventoryProgressText(
  loaded: number,
  total: number,
  hasInventoryRevision: boolean,
  loading: boolean,
): string {
  const totalLabel = total > 0 || hasInventoryRevision
    ? String(total)
    : 'unbekannt';
  const loadingLabel = loading
    ? ' · weitere Seiten werden automatisch geladen…'
    : '';
  return `Domain-Inventar: ${loaded} / ${totalLabel} Bereiche geladen${loadingLabel}`;
}

export function semanticScopeNoticeText(
  state: FullGraphLoadState,
  domainSelected: boolean,
  evidence: Readonly<CodeCompassSemanticScopeEvidence> | null,
): string {
  if (state !== 'complete') return '';
  if (!domainSelected) {
    return 'Basisindex vollständig übertragen; semantische Vollständigkeit wird nur für ausgewählte Domains verifiziert.';
  }
  if (!evidence) {
    return 'Transport vollständig, Semantik nicht verifiziert.';
  }
  if (evidence.complete === true) {
    return `Adapter-Evidenz vollständig: ${evidence.supplementNodeCount.toLocaleString('de-DE')} Symbole · ${evidence.supplementEdgeCount.toLocaleString('de-DE')} semantische Relationen.`;
  }
  if (evidence.status === 'unavailable') {
    return 'Transport vollständig, semantisches Supplement nicht verfügbar.';
  }
  return 'Transport vollständig, semantisches Supplement unvollständig.';
}
