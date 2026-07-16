import { Injectable, inject } from '@angular/core';
import { Observable, catchError, forkJoin, map, of } from 'rxjs';

import { ModelTrainingApiService } from '../model-training/model-training-api.service';
import { entityFrom, normalizeDatasetSummary, normalizePage } from '../model-training/model-training-normalizers';
import {
  DatasetSummary,
  TrainingBaseModel,
  TrainingCapabilities,
  TrainingGpuProfile,
} from '../model-training/model-training.models';
import { AgentDirectoryService } from '../../services/agent-directory.service';

export interface VpModelTrainingOptions {
  hubAvailable: boolean;
  datasets: DatasetSummary[];
  trainingProfiles: TrainingGpuProfile[];
  baseModels: TrainingBaseModel[];
}

const EMPTY_OPTIONS: VpModelTrainingOptions = {
  hubAvailable: false,
  datasets: [],
  trainingProfiles: [],
  baseModels: [],
};

@Injectable({ providedIn: 'root' })
export class VpModelTrainingOptionsService {
  private readonly directory = inject(AgentDirectoryService);
  private readonly api = inject(ModelTrainingApiService);

  load(): Observable<VpModelTrainingOptions> {
    const hubUrl = String(
      this.directory.list().find(agent => agent.role === 'hub')?.url
      ?? this.directory.list().find(agent => agent.name === 'hub')?.url
      ?? '',
    ).replace(/\/+$/, '');
    if (!hubUrl) return of({ ...EMPTY_OPTIONS });

    return forkJoin({
      capabilities: this.api.capabilities(hubUrl).pipe(catchError(() => of(null))),
      datasets: this.api.listDatasets(hubUrl, { limit: 100 }).pipe(catchError(() => of([]))),
    }).pipe(map(({ capabilities, datasets }) => {
      const normalizedCapabilities = capabilities
        ? entityFrom(capabilities, 'capabilities') as TrainingCapabilities
        : null;
      const datasetPage = normalizePage(datasets, ['datasets'], normalizeDatasetSummary);
      return {
        hubAvailable: true,
        datasets: datasetPage.items,
        trainingProfiles: Array.isArray(normalizedCapabilities?.gpu_profiles)
          ? normalizedCapabilities.gpu_profiles
          : [],
        baseModels: Array.isArray(normalizedCapabilities?.base_models)
          ? normalizedCapabilities.base_models
          : [],
      };
    }));
  }
}
