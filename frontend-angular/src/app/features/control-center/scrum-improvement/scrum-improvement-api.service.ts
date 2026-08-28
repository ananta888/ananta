import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { ApiBaseService } from '../../../services/api-base.service';
import { ScrumImprovementOverview } from './scrum-improvement.models';

@Injectable({ providedIn: 'root' })
export class ScrumImprovementApiService extends ApiBaseService {
  overview(hubUrl: string, scopeId: string): Observable<ScrumImprovementOverview> {
    const url = `${hubUrl}/api/scrum/overview?scope_id=${encodeURIComponent(scopeId)}`;
    return this.core.get<ScrumImprovementOverview>(url, hubUrl, undefined, false);
  }
}
