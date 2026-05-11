import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiBaseService } from './api-base.service';
import { WorkerRuntimeCandidate, RuntimeTarget, DestinationOption } from '../models/worker-runtime-target.model';

@Injectable({ providedIn: 'root' })
export class WorkerRuntimeCandidateApiService extends ApiBaseService {

  listWorkerCandidates(baseUrl: string, token?: string): Observable<WorkerRuntimeCandidate[]> {
    return this.core.get<WorkerRuntimeCandidate[]>(`${baseUrl}/api/worker-candidates`, baseUrl, token);
  }

  listRuntimeTargets(baseUrl: string, token?: string): Observable<RuntimeTarget[]> {
    return this.core.get<RuntimeTarget[]>(`${baseUrl}/api/runtime-targets`, baseUrl, token);
  }

  listDestinationOptions(baseUrl: string, token?: string): Observable<DestinationOption[]> {
    return this.core.get<DestinationOption[]>(`${baseUrl}/api/destination-options`, baseUrl, token);
  }
}
