import { Injectable } from '@angular/core';

export interface KnowledgeGraphSignal {
  marker: 'none' | 'knowledge-conflict' | 'curated-wiki';
  color: string;
  stale: boolean;
  navigationPath: string | null;
}

@Injectable({ providedIn: 'root' })
export class KnowledgeHygieneGraphSignalService {
  project(node: { kind?: string; id?: string; metadata?: Record<string, unknown> }): KnowledgeGraphSignal {
    const kind = String(node.kind ?? node.metadata?.['kind'] ?? '');
    const projectId = String(node.metadata?.['project_id'] ?? '');
    const state = String(node.metadata?.['state'] ?? '');
    const revision = Number(node.metadata?.['revision'] ?? 0);
    const latestRevision = Number(node.metadata?.['latest_revision'] ?? revision);
    if (kind === 'knowledge_conflict') {
      const conflictId = String(node.metadata?.['conflict_id'] ?? node.id ?? '').replace(/^conflict:/, '');
      return {
        marker: 'knowledge-conflict',
        color: state === 'resolved' ? '#678d73' : '#bd4d2b',
        stale: revision < latestRevision,
        navigationPath: projectId && conflictId ? '/knowledge-hygiene/' + encodeURIComponent(projectId) + '?conflict=' + encodeURIComponent(conflictId) : null,
      };
    }
    if (kind === 'curated_wiki') {
      return {
        marker: 'curated-wiki',
        color: '#2d6b62',
        stale: revision < latestRevision,
        navigationPath: projectId ? '/knowledge-hygiene/' + encodeURIComponent(projectId) + '?tab=wiki' : null,
      };
    }
    return { marker: 'none', color: 'inherit', stale: false, navigationPath: null };
  }
}
