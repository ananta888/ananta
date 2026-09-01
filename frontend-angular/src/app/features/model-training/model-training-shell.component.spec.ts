import { Component, EventEmitter, Input, Output, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, ParamMap } from '@angular/router';
import { BehaviorSubject } from 'rxjs';

import { ModelTrainingFacade } from './model-training.facade';
import { ModelTrainingJobMonitorService } from './model-training-job-monitor.service';
import { TrainingCapabilities } from './model-training.models';
import { ModelTrainingShellComponent } from './model-training-shell.component';
import { UnslothCapabilityPanelComponent } from './unsloth/unsloth-capability-panel.component';

@Component({
  selector: 'app-unsloth-capability-panel',
  standalone: true,
  template: '',
})
class UnslothCapabilityPanelStubComponent {
  @Input() capabilities: TrainingCapabilities | null = null;
  @Input() hubUrl = '';
  @Input() storage: unknown | null = null;
  @Input() storageLoading = false;
  @Output() readonly storageRefresh = new EventEmitter<void>();
}

describe('ModelTrainingShellComponent', () => {
  let queryParamMap: BehaviorSubject<ParamMap>;
  const capabilities = signal<TrainingCapabilities | null>({
    available: true,
    backends: [{ id: 'peft', available: true }],
    gpu_profiles: [{ id: 'gpu-1', available: true }],
    base_models: [{ id: 'model-1', local: true, available: true, compatible_backends: ['peft'] }],
    limits: {},
  });
  const monitor = { job: signal(null) };
  const facade = {
    hubUrl: signal('http://hub.test'),
    capabilities,
    datasets: signal([]),
    datasetCount: signal(0),
    selectedDataset: signal(null),
    jobs: signal([]),
    jobCount: signal(0),
    adapters: signal([]),
    selectedAdapter: signal(null),
    unslothStorage: signal(null),
    loadingCapabilities: signal(false),
    loadingDatasets: signal(false),
    loadingJobs: signal(false),
    loadingAdapters: signal(false),
    loadingUnslothStorage: signal(false),
    error: signal(''),
    monitor,
    loadOverview: vi.fn(),
    clearError: vi.fn(),
    resolveHub: vi.fn(() => 'http://hub.test'),
    loadDatasets: vi.fn(),
    loadUnslothStorage: vi.fn(),
    selectDataset: vi.fn(),
    selectJob: vi.fn(),
    selectAdapterById: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    facade.error.set('');
    queryParamMap = new BehaviorSubject(convertToParamMap({}));
    TestBed.configureTestingModule({
      imports: [ModelTrainingShellComponent],
      providers: [{ provide: ActivatedRoute, useValue: { queryParamMap } }],
    });
    TestBed.overrideComponent(ModelTrainingShellComponent, {
      remove: {
        imports: [UnslothCapabilityPanelComponent],
        providers: [ModelTrainingFacade, ModelTrainingJobMonitorService],
      },
      add: {
        imports: [UnslothCapabilityPanelStubComponent],
        providers: [
          { provide: ModelTrainingFacade, useValue: facade },
          { provide: ModelTrainingJobMonitorService, useValue: monitor },
        ],
      },
    });
  });

  it('renders the Hub-governed four-area control center and loads its overview', () => {
    const fixture = TestBed.createComponent(ModelTrainingShellComponent);
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent || '';

    expect(facade.loadOverview).toHaveBeenCalledTimes(1);
    expect(text).toContain('LoRA-/QLoRA-Control-Center');
    expect(text).toContain('Datasets');
    expect(text).toContain('Training starten');
    expect(text).toContain('Jobs & Fortschritt');
    expect(text).toContain('Adapter & Evaluation');
    expect(text).toContain('Governance bleibt beim Hub');
  });

  it('refreshes only through the resolved Hub facade', () => {
    const fixture = TestBed.createComponent(ModelTrainingShellComponent);
    fixture.detectChanges();
    vi.clearAllMocks();

    fixture.componentInstance.refresh();

    expect(facade.clearError).toHaveBeenCalledOnce();
    expect(facade.resolveHub).toHaveBeenCalledOnce();
    expect(facade.loadOverview).toHaveBeenCalledOnce();
  });

  it('opens and selects a job from a bounded job_id query parameter', () => {
    queryParamMap.next(convertToParamMap({ tab: 'datasets', job_id: 'job-vp-001' }));
    const fixture = TestBed.createComponent(ModelTrainingShellComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.activeTab()).toBe('jobs');
    expect(facade.selectJob).toHaveBeenCalledWith('job-vp-001');
    expect(facade.selectDataset).not.toHaveBeenCalled();
  });

  it('opens and selects a dataset from dataset_id', () => {
    queryParamMap.next(convertToParamMap({ dataset_id: 'dataset:17' }));
    const fixture = TestBed.createComponent(ModelTrainingShellComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.activeTab()).toBe('datasets');
    expect(facade.selectDataset).toHaveBeenCalledWith('dataset:17');
  });

  it('opens the adapter lifecycle from a bounded adapter_id', () => {
    queryParamMap.next(convertToParamMap({ adapter_id: 'adapter:17' }));
    const fixture = TestBed.createComponent(ModelTrainingShellComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.activeTab()).toBe('adapters');
    expect(facade.selectAdapterById).toHaveBeenCalledWith('adapter:17');
  });

  it('honors valid tabs and ignores malformed entity identifiers', () => {
    queryParamMap.next(convertToParamMap({ tab: 'jobs', job_id: '../job', dataset_id: 'a/b' }));
    const fixture = TestBed.createComponent(ModelTrainingShellComponent);
    fixture.detectChanges();

    expect(fixture.componentInstance.activeTab()).toBe('jobs');
    expect(facade.selectJob).not.toHaveBeenCalled();
    expect(facade.selectDataset).not.toHaveBeenCalled();
  });

  it('implements arrow-key navigation for the ARIA tablist', async () => {
    const fixture = TestBed.createComponent(ModelTrainingShellComponent);
    fixture.detectChanges();
    const firstTab = fixture.nativeElement.querySelector('[role="tab"]') as HTMLButtonElement;
    firstTab.focus();
    firstTab.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    fixture.detectChanges();
    await Promise.resolve();

    expect(fixture.componentInstance.activeTab()).toBe('training');
    expect((fixture.nativeElement.querySelectorAll('[role="tab"]')[1] as HTMLButtonElement).tabIndex).toBe(0);
  });

  it.each([
    'Bitte neu anmelden und den Vorgang erneut starten.',
    'Diese Aktion benötigt Administratorrechte.',
    'Daten aktualisieren, Ergebnis prüfen und erneut ausführen.',
    'Der Upload überschreitet das Größenlimit.',
    'Markierte Daten korrigieren und erneut validieren.',
    'Der Service ist vorübergehend nicht verfügbar.',
  ])('renders an actionable recovery path: %s', message => {
    facade.error.set(message);
    const fixture = TestBed.createComponent(ModelTrainingShellComponent);
    fixture.detectChanges();

    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain(message);
    const retry = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('button'),
    ).find(button => button.textContent?.includes('Erneut laden')) as HTMLButtonElement;
    expect(retry).toBeTruthy();
    vi.clearAllMocks();
    retry.click();
    expect(facade.loadOverview).toHaveBeenCalledOnce();
  });
});
