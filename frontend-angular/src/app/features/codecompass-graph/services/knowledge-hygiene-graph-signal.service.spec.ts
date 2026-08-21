import { KnowledgeHygieneGraphSignalService } from './knowledge-hygiene-graph-signal.service';

describe('KnowledgeHygieneGraphSignalService', () => {
  const service = new KnowledgeHygieneGraphSignalService();

  it('projects a distinct conflict marker and scoped navigation', () => {
    expect(service.project({
      kind: 'knowledge_conflict',
      id: 'conflict:KHC_example',
      metadata: { project_id: 'project-a', state: 'open', revision: 1, latest_revision: 2 },
    })).toEqual({
      marker: 'knowledge-conflict',
      color: '#bd4d2b',
      stale: true,
      navigationPath: '/knowledge-hygiene/project-a?conflict=KHC_example',
    });
  });

  it('does not decorate unrelated CodeCompass nodes', () => {
    expect(service.project({ kind: 'symbol', id: 'symbol-1' }).marker).toBe('none');
  });
});
