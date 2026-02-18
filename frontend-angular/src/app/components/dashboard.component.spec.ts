import { of, throwError } from 'rxjs';

import { DashboardComponent } from './dashboard.component';

describe('DashboardComponent (benchmarks)', () => {
  const hubApiMock = {
    getLlmBenchmarks: vi.fn(),
  };

  function createComponent(): DashboardComponent {
    const cmp = Object.create(DashboardComponent.prototype) as DashboardComponent & { hubApi: any };
    cmp.hub = { name: 'hub', url: 'http://hub:5000', role: 'hub' } as any;
    cmp.benchmarkTaskKind = 'analysis';
    cmp.benchmarkData = [];
    cmp.benchmarkUpdatedAt = null;
    cmp.hubApi = hubApiMock;
    return cmp;
  }

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads benchmark recommendations and updated timestamp', () => {
    hubApiMock.getLlmBenchmarks.mockReturnValue(
      of({
        updated_at: 1739790000,
        items: [{ id: 'aider:gpt-4o-mini', provider: 'aider', model: 'gpt-4o-mini', focus: { suitability_score: 88.5 } }],
      })
    );

    const cmp = createComponent();
    cmp.benchmarkTaskKind = 'coding';
    cmp.refreshBenchmarks();

    expect(hubApiMock.getLlmBenchmarks).toHaveBeenCalledWith('http://hub:5000', { task_kind: 'coding', top_n: 8 });
    expect(cmp.benchmarkData.length).toBe(1);
    expect(cmp.benchmarkData[0].provider).toBe('aider');
    expect(cmp.benchmarkUpdatedAt).toBe(1739790000);
  });

  it('falls back to empty benchmark list on API error', () => {
    hubApiMock.getLlmBenchmarks.mockReturnValue(throwError(() => new Error('offline')));

    const cmp = createComponent();
    cmp.benchmarkData = [{ id: 'old' }];
    cmp.benchmarkUpdatedAt = 1;
    cmp.refreshBenchmarks();

    expect(cmp.benchmarkData).toEqual([]);
    expect(cmp.benchmarkUpdatedAt).toBe(1);
  });
});
