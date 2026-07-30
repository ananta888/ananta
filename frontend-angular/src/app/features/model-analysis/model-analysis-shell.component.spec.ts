import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ModelAnalysisFacade } from './model-analysis.facade';
import { ModelAnalysisShellComponent } from './model-analysis-shell.component';
import { ModelAnalysisViewState } from './model-analysis.models';

describe('ModelAnalysisShellComponent', () => {
  const facade = {
    viewState: signal<ModelAnalysisViewState>('loading'),
    hubUrl: signal('http://hub.test'),
    capabilities: signal<any>(null),
    jobs: signal<any[]>([]),
    nextCursor: signal<string | null>(null),
    selectedJob: signal<any>(null),
    report: signal<any>(null),
    graph: signal<any>(null),
    loadingOverview: signal(false),
    loadingSelection: signal(false),
    mutating: signal(false),
    error: signal(''),
    stateReasonCode: signal('model_analysis_loading'),
    loadOverview: vi.fn(),
    loadMore: vi.fn(),
    start: vi.fn(),
    selectJob: vi.fn(),
    refreshSelected: vi.fn(),
    cancelSelected: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    facade.viewState.set('loading');
    facade.capabilities.set(null);
    facade.jobs.set([]);
    facade.selectedJob.set(null);
    facade.report.set(null);
    facade.graph.set(null);
    facade.error.set('');
    facade.stateReasonCode.set('model_analysis_loading');
    TestBed.configureTestingModule({ imports: [ModelAnalysisShellComponent] });
    TestBed.overrideComponent(ModelAnalysisShellComponent, {
      remove: { providers: [ModelAnalysisFacade] },
      add: { providers: [{ provide: ModelAnalysisFacade, useValue: facade }] },
    });
  });

  it.each([
    ['loading', 'model-analysis-loading', 'Analysen werden geladen'],
    ['empty', 'model-analysis-empty', 'Noch keine Analysejobs'],
    ['unsupported', 'model-analysis-unsupported', 'Modellanalyse nicht unterstützt'],
    ['permission', 'model-analysis-permission', 'Keine Berechtigung'],
    ['error', 'model-analysis-error', 'Analyseoberfläche nicht verfügbar'],
  ] as const)('renders a distinct %s state', (state, testId, text) => {
    facade.viewState.set(state);
    facade.stateReasonCode.set(`model_analysis_${state}`);
    if (state === 'unsupported') {
      facade.capabilities.set({ supported: false, reason_code: 'runtime_unavailable' });
    }
    if (state === 'error') facade.error.set('Hub nicht erreichbar');
    const fixture = TestBed.createComponent(ModelAnalysisShellComponent);
    fixture.detectChanges();

    const element = fixture.nativeElement as HTMLElement;
    expect(element.querySelector(`[data-testid="${testId}"]`)).toBeTruthy();
    expect(element.textContent).toContain(text);
    expect(element.textContent).toContain(`model_analysis_${state}`);
  });

  it('offers labelled native controls for start, selection and cancellation', () => {
    facade.viewState.set('ready');
    facade.jobs.set([job('running')]);
    facade.selectedJob.set(job('running'));
    const fixture = TestBed.createComponent(ModelAnalysisShellComponent);
    fixture.detectChanges();
    fixture.componentInstance.importRef = 'import:model-1';
    fixture.componentInstance.start();

    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('main[aria-labelledby="analysis-title"]')).toBeTruthy();
    expect(root.querySelector('label[for="analysis-import-ref"]')?.textContent).toContain('Importreferenz');
    expect(root.querySelector('[aria-live="polite"]')).toBeTruthy();
    expect(root.querySelector('a[href="/docs/model-intelligence/interpretation.md"]')).toBeTruthy();
    expect(root.querySelector('progress[aria-label]')).toBeTruthy();
    expect(facade.start).toHaveBeenCalledWith('import:model-1');

    const cancel = Array.from(root.querySelectorAll('button')).find(
      button => button.textContent?.includes('Job abbrechen'),
    ) as HTMLButtonElement;
    cancel.click();
    expect(facade.cancelSelected).toHaveBeenCalledOnce();
  });
});

function job(status: string): any {
  return {
    job_id: 'job-1',
    model_id: 'model-1',
    profile_id: 'bounded-ui',
    status,
    progress_percent: 42,
  };
}
