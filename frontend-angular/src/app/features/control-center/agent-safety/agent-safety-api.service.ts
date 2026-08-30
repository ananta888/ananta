import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { ApiBaseService } from '../../../services/api-base.service';
import { AgentSafetyOverview, AgentSafetyPolicyCommand, AgentSafetyPolicyView } from './agent-safety.models';

@Injectable({ providedIn: 'root' })
export class AgentSafetyApiService extends ApiBaseService {
  overview(hubUrl: string, projectId: string, runId?: string): Observable<AgentSafetyOverview> {
    const query = new URLSearchParams({ project_id: projectId });
    if (runId) query.set('run_id', runId);
    return this.core.get<AgentSafetyOverview>(
      `${hubUrl}/api/agent-safety/overview?${query.toString()}`,
      hubUrl,
      undefined,
      false,
    );
  }

  configurePolicy(hubUrl: string, command: AgentSafetyPolicyCommand): Observable<AgentSafetyPolicyView> {
    return this.core.post<AgentSafetyPolicyView>(
      `${hubUrl}/api/agent-safety/policies`, command, hubUrl, undefined, false,
    );
  }
}
