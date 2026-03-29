import { of } from 'rxjs';

import { TaskDetailComponent } from './task-detail.component';

describe('TaskDetailComponent', () => {
  function createComponent(): TaskDetailComponent {
    const cmp = Object.create(TaskDetailComponent.prototype) as TaskDetailComponent & { hubApi: any; ns: any; hub: any };
    cmp.hubApi = { reviewTaskProposal: vi.fn(() => of({})) };
    cmp.ns = { success: vi.fn(), error: vi.fn(), fromApiError: vi.fn((_e: any, fallback: string) => fallback) };
    cmp.hub = { url: 'http://hub:5000' };
    cmp.task = {
      worker_execution_context: {
        context: { context_text: 'Use repo context', chunks: [{ id: 'c1' }] },
        allowed_tools: ['codex', 'sgpt'],
        expected_output_schema: { type: 'object', required: ['summary'] },
      },
      last_proposal: {
        review: { required: true, status: 'pending' },
        research_artifact: { sources: [{ url: 'https://example.com', title: 'Example' }] },
      },
      history: [
        { event_type: 'proposal_result', timestamp: 1, reason: 'planned' },
        { event_type: 'execution_result', timestamp: 2, reason: 'done', cost_summary: { cost_units: 1.25, tokens_total: 640, latency_ms: 1200, provider: 'openai', model: 'gpt-4o-mini' } },
        { event_type: 'other', timestamp: 3 },
      ],
    };
    cmp.busy = false;
    return cmp;
  }

  it('exposes worker context, schema and provenance helpers', () => {
    const cmp = createComponent();

    expect(cmp.workerContextText()).toBe('Use repo context');
    expect(cmp.allowedTools()).toEqual(['codex', 'sgpt']);
    expect(cmp.expectedSchema()).toEqual({ type: 'object', required: ['summary'] });
    expect(cmp.researchSources().length).toBe(1);
    expect(cmp.provenanceEvents().length).toBe(2);
  });

  it('reviews proposals through hub api and reloads task', () => {
    const cmp = createComponent();
    cmp.reload = vi.fn();
    Object.defineProperty(cmp, 'tid', { value: 'T-1', configurable: true });

    cmp.reviewProposal('approve');

    expect(cmp.hubApi.reviewTaskProposal).toHaveBeenCalledWith('http://hub:5000', 'T-1', { action: 'approve' });
    expect(cmp.ns.success).toHaveBeenCalledWith('Vorschlag freigegeben');
    expect(cmp.reload).toHaveBeenCalled();
  });
});
