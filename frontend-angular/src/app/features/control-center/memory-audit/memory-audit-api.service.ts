import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { ApiBaseService } from '../../../services/api-base.service';
import { MemoryAuditFilters, MemoryAuditPage, MemoryDiffPage } from './memory-audit.models';

@Injectable({ providedIn: 'root' })
export class MemoryAuditApiService extends ApiBaseService {
  diffs(hubUrl: string, projectId: string, filters: MemoryAuditFilters): Observable<MemoryDiffPage> {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value) query.set(key, value);
    }
    return this.core.get<MemoryDiffPage>(
      `${hubUrl}/api/projects/${encodeURIComponent(projectId)}/memory-audit/diffs?${query.toString()}`,
      hubUrl, undefined, false,
    );
  }

  events(hubUrl: string, projectId: string, filters: MemoryAuditFilters = {},
    afterSequence = 0): Observable<MemoryAuditPage> {
    const query = new URLSearchParams({ page_size: '50', after_sequence: String(afterSequence) });
    for (const [key, value] of Object.entries(filters)) {
      if (value) query.set(key, value);
    }
    return this.core.get<MemoryAuditPage>(
      `${hubUrl}/api/projects/${encodeURIComponent(projectId)}/memory-audit/events?${query.toString()}`,
      hubUrl, undefined, false,
    );
  }
}
