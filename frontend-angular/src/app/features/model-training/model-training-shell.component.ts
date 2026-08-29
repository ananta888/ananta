import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, ParamMap } from '@angular/router';
import { Subscription } from 'rxjs';

import { ExplanationNoticeComponent, SummaryMetric, SummaryPanelComponent } from '../../shared/ui/display';
import { PageIntroComponent } from '../../shared/ui/layout';
import { ErrorStateComponent, LoadingStateComponent, StatusBadgeComponent } from '../../shared/ui/state';
import { AdapterImportComponent } from './adapters/adapter-import.component';
import { AdapterRegistryComponent } from './adapters/adapter-registry.component';
import { AdapterRuntimeManagementComponent } from './adapters/adapter-runtime-management.component';
import { DatasetCatalogComponent } from './datasets/dataset-catalog.component';
import { DatasetDetailComponent } from './datasets/dataset-detail.component';
import { DatasetManagementComponent } from './datasets/dataset-management.component';
import { DatasetUploadComponent } from './datasets/dataset-upload.component';
import { EvaluationPanelComponent } from './evaluation/evaluation-panel.component';
import { TrainingJobDetailComponent } from './jobs/training-job-detail.component';
import { TrainingJobListComponent } from './jobs/training-job-list.component';
import { ModelTrainingFacade } from './model-training.facade';
import { ModelTrainingJobMonitorService } from './model-training-job-monitor.service';
import { ModelTrainingTab, TrainingJobAcceptance } from './model-training.models';
import { TrainingWizardComponent } from './training-wizard/training-wizard.component';
import { DendriticMemoryWorkbenchComponent } from './training-wizard/dendritic-memory-workbench.component';
import { UnslothCapabilityPanelComponent } from './unsloth/unsloth-capability-panel.component';

@Component({
  selector: 'app-model-training-shell',
  standalone: true,
  imports: [
    AdapterImportComponent,
    AdapterRegistryComponent,
    AdapterRuntimeManagementComponent,
    DatasetCatalogComponent,
    DatasetDetailComponent,
    DatasetManagementComponent,
    DatasetUploadComponent,
    ErrorStateComponent,
    EvaluationPanelComponent,
    ExplanationNoticeComponent,
    LoadingStateComponent,
    PageIntroComponent,
    StatusBadgeComponent,
    SummaryPanelComponent,
    TrainingJobDetailComponent,
    TrainingJobListComponent,
    TrainingWizardComponent,
    DendriticMemoryWorkbenchComponent,
    UnslothCapabilityPanelComponent,
  ],
  providers: [ModelTrainingFacade, ModelTrainingJobMonitorService],
  template: `
    <main class="training-control-center" data-testid="model-training-control-center">
      <app-page-intro
        eyebrow="Lokales Modelltraining"
        title="LoRA-/QLoRA-Control-Center"
        subtitle="Datasets, Trainingsjobs, Evaluation und Adapter-Freigaben zentral über den Hub verwalten.">
        <button intro-actions type="button" class="secondary" (click)="refresh()" [disabled]="loading()">
          Alles aktualisieren
        </button>
      </app-page-intro>

      @if (!facade.hubUrl()) {
        <app-error-state
          title="Kein Hub verfügbar"
          message="Das Modelltraining benötigt einen erreichbaren Hub aus der Agent Directory. Es wird keine Worker-URL direkt angesprochen."
          retryLabel="Hub erneut suchen"
          (retry)="refresh()" />
      } @else {
        @if (facade.error()) {
          <app-error-state
            title="Training-Control-Center nur teilweise geladen"
            [message]="facade.error()"
            retryLabel="Erneut laden"
            secondaryLabel="Meldung schließen"
            (retry)="refresh()"
            (secondary)="facade.clearError()" />
        }

        @if (initialLoading()) {
          <app-loading-state label="Training-Capabilities und Kataloge werden vom Hub geladen" [count]="3" [columns]="3" />
        } @else {
          <app-summary-panel
            eyebrow="Hub Control Plane"
            title="Trainingsbereitschaft"
            [summary]="capabilitySummary()"
            [metrics]="summaryMetrics()"
            [columns]="3">
            <div class="capability-row">
              <app-status-badge
                [label]="facade.capabilities()?.available ? 'Training verfügbar' : 'Training nicht verfügbar'"
                [tone]="facade.capabilities()?.available ? 'success' : 'warning'"
                [dot]="true" />
              <span class="muted font-sm">{{ facade.capabilities()?.reason_code || 'Hub-Policy und lokale Ressourcen wurden abgefragt.' }}</span>
            </div>
          </app-summary-panel>
        }

        <app-explanation-notice
          title="Governance bleibt beim Hub"
          message="Uploads, Queueing, Training, Evaluation, Lifecycle-Aktionen und Exporte laufen ausschließlich über die Hub-API. Live-Training und Adapter-Freigaben benötigen explizite Bestätigungen."
          tone="info" />

        <app-unsloth-capability-panel
          [capabilities]="facade.capabilities()"
          [hubUrl]="facade.hubUrl()"
          [storage]="facade.unslothStorage()"
          [storageLoading]="facade.loadingUnslothStorage()"
          (storageRefresh)="facade.loadUnslothStorage()"
        />

        <nav class="training-tabs" aria-label="Bereiche des Modelltrainings" role="tablist">
          @for (tab of tabs; track tab.id) {
            <button
              type="button"
              role="tab"
              class="secondary"
              [id]="'training-tab-' + tab.id"
              [class.active]="activeTab() === tab.id"
              [attr.aria-selected]="activeTab() === tab.id"
              [attr.aria-controls]="'training-panel-' + tab.id"
              [attr.tabindex]="activeTab() === tab.id ? 0 : -1"
              (click)="activeTab.set(tab.id)"
              (keydown)="onTabKeydown($event, $index)">
              {{ tab.label }}
            </button>
          }
        </nav>

        <section
          class="training-tab-panel"
          role="tabpanel"
          [id]="'training-panel-' + activeTab()"
          [attr.aria-labelledby]="'training-tab-' + activeTab()"
          [attr.aria-label]="activeTabLabel()">
          @switch (activeTab()) {
            @case ('datasets') {
              <div class="training-grid training-grid-narrow">
                <app-model-training-dataset-upload />
                <app-model-training-dataset-catalog />
              </div>
              @if (facade.selectedDataset()) {
                <app-model-training-dataset-detail />
                <app-model-training-dataset-management />
              }
            }
            @case ('training') {
              <app-model-training-wizard (jobCreated)="showAcceptedJob($event)" />
              @if (facade.capabilities()?.dendritic_memory_experiment?.available) {
                <app-dendritic-memory-workbench
                  [hubUrl]="facade.hubUrl()"
                  [capability]="facade.capabilities()?.dendritic_memory_experiment" />
              }
            }
            @case ('jobs') {
              <app-model-training-job-list />
              @if (facade.monitor.job()) {
                <app-model-training-job-detail />
              }
            }
            @case ('adapters') {
              <div class="training-grid">
                <app-model-training-adapter-import />
                <app-model-training-adapter-registry />
              </div>
              <app-model-training-adapter-runtime-management />
              <app-model-training-evaluation-panel />
            }
          }
        </section>
      }
    </main>
  `,
  styles: [`
    .training-control-center { display:grid; gap:16px; }
    .training-tabs { display:flex; gap:8px; flex-wrap:wrap; border-bottom:1px solid var(--border); padding-bottom:10px; }
    .training-tabs button.active { color:var(--accent); border-color:var(--accent); background:color-mix(in srgb,var(--accent) 10%,transparent); }
    .training-tab-panel { display:grid; gap:16px; min-width:0; }
    .training-grid { display:grid; grid-template-columns:minmax(280px,0.8fr) minmax(0,1.5fr); gap:16px; align-items:start; }
    .training-grid-narrow { grid-template-columns:minmax(280px,0.7fr) minmax(0,1.8fr); }
    .capability-row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:12px; }
    @media (max-width:900px) { .training-grid,.training-grid-narrow { grid-template-columns:1fr; } }
  `],
})
export class ModelTrainingShellComponent implements OnInit, OnDestroy {
  readonly facade = inject(ModelTrainingFacade);
  private readonly route = inject(ActivatedRoute);
  private readonly queryParamsSubscription = new Subscription();
  readonly activeTab = signal<ModelTrainingTab>('datasets');
  readonly tabs: ReadonlyArray<{ id: ModelTrainingTab; label: string }> = [
    { id: 'datasets', label: 'Datasets' },
    { id: 'training', label: 'Training starten' },
    { id: 'jobs', label: 'Jobs & Fortschritt' },
    { id: 'adapters', label: 'Adapter & Evaluation' },
  ];

  readonly loading = computed(() =>
    this.facade.loadingCapabilities()
    || this.facade.loadingDatasets()
    || this.facade.loadingJobs()
    || this.facade.loadingAdapters()
    || this.facade.loadingUnslothStorage(),
  );
  readonly initialLoading = computed(() => this.loading() && !this.facade.capabilities());
  readonly summaryMetrics = computed<SummaryMetric[]>(() => {
    const capabilities = this.facade.capabilities();
    return [
      { label: 'Datasets', value: this.facade.datasetCount(), hint: 'Versionierte Imports' },
      { label: 'Trainingsjobs', value: this.facade.jobCount(), hint: 'Queue und Historie' },
      { label: 'Adapter', value: this.facade.adapters().length, hint: 'Registry-Einträge' },
      { label: 'Backends', value: capabilities?.backends.filter(item => item.available).length || 0, hint: 'Verfügbar' },
      { label: 'GPU-Profile', value: capabilities?.gpu_profiles.filter(item => item.available).length || 0, hint: 'Verfügbar' },
      { label: 'Basismodelle', value: capabilities?.base_models.filter(item => item.available !== false).length || 0, hint: 'Lokal kompatibel' },
    ];
  });
  readonly capabilitySummary = computed(() => {
    const capabilities = this.facade.capabilities();
    if (!capabilities) return 'Capabilities wurden noch nicht geladen.';
    return capabilities.available
      ? 'Der Hub meldet mindestens einen zulässigen lokalen Trainingspfad.'
      : 'Der Hub blockiert echtes Training aktuell; Dry-Run, Dataset-Prüfung und Registry bleiben nachvollziehbar.';
  });
  readonly activeTabLabel = computed(() => this.tabs.find(tab => tab.id === this.activeTab())?.label || 'Modelltraining');
  ngOnInit(): void {
    this.queryParamsSubscription.add(
      this.route.queryParamMap.subscribe(params => this.applyQuerySelection(params)),
    );
    this.facade.loadOverview();
  }

  ngOnDestroy(): void {
    this.queryParamsSubscription.unsubscribe();
  }

  refresh(): void {
    this.facade.clearError();
    this.facade.resolveHub();
    this.facade.loadOverview();
  }

  showAcceptedJob(accepted: TrainingJobAcceptance): void {
    this.activeTab.set('jobs');
    if (accepted.job_id) this.facade.selectJob(accepted.job_id);
  }

  onTabKeydown(event: KeyboardEvent, index: number): void {
    let nextIndex = index;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (index + 1) % this.tabs.length;
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (index - 1 + this.tabs.length) % this.tabs.length;
    else if (event.key === 'Home') nextIndex = 0;
    else if (event.key === 'End') nextIndex = this.tabs.length - 1;
    else return;

    event.preventDefault();
    this.activeTab.set(this.tabs[nextIndex].id);
    const buttons = (event.currentTarget as HTMLElement | null)?.parentElement?.querySelectorAll<HTMLElement>('[role="tab"]');
    queueMicrotask(() => buttons?.[nextIndex]?.focus());
  }

  private applyQuerySelection(params: ParamMap): void {
    const requestedTab = params.get('tab');
    const tab = this.tabs.find(candidate => candidate.id === requestedTab)?.id;
    if (tab) this.activeTab.set(tab);

    const jobId = this.validEntityId(params.get('job_id'));
    if (jobId) {
      this.activeTab.set('jobs');
      this.facade.selectJob(jobId);
      return;
    }

    const datasetId = this.validEntityId(params.get('dataset_id'));
    if (datasetId) {
      this.activeTab.set('datasets');
      this.facade.selectDataset(datasetId);
    }
  }

  private validEntityId(value: string | null): string {
    const candidate = String(value || '').trim();
    return /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$/.test(candidate) ? candidate : '';
  }
}
