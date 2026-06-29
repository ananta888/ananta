import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { CaseArtifact } from '../caseflow.models';
import {
  JobApplicationCase,
  JobFitScore,
} from './job-application.models';

@Injectable({ providedIn: 'root' })
export class JobApplicationApiService {
  private readonly http = inject(HttpClient);
  private readonly dir = inject(AgentDirectoryService);

  private get base(): string {
    const hub = this.dir.list().find(a => a.role === 'hub');
    return `${hub?.url ?? 'http://127.0.0.1:5000'}/api/caseflow/jobs`;
  }

  listJobApplications(
    filters: Record<string, string> = {}
  ): Observable<JobApplicationCase[]> {
    let params = new HttpParams();
    Object.entries(filters).forEach(([k, v]) => {
      if (v) params = params.set(k, v);
    });
    return this.http.get<JobApplicationCase[]>(this.base, { params });
  }

  getJobApplication(caseId: string): Observable<JobApplicationCase> {
    return this.http.get<JobApplicationCase>(`${this.base}/${caseId}`);
  }

  getFitScore(caseId: string): Observable<JobFitScore> {
    return this.http.get<JobFitScore>(`${this.base}/${caseId}/fit-score`);
  }

  setManualFitScore(
    caseId: string,
    score: number,
    reason: string
  ): Observable<JobFitScore> {
    return this.http.put<JobFitScore>(`${this.base}/${caseId}/fit-score`, {
      score,
      reason,
    });
  }

  getDocumentBundle(caseId: string): Observable<unknown> {
    return this.http.get(`${this.base}/${caseId}/document-bundle`);
  }

  addPosting(caseId: string, rawText: string, sourceUrl?: string): Observable<CaseArtifact> {
    return this.http.post<CaseArtifact>(`${this.base}/${caseId}/posting`, {
      raw_text: rawText,
      source_url: sourceUrl,
    });
  }
}
