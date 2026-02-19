import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class HubAutomationApiClient {
  private core = inject(HubApiCoreService);
  getAutopilotStatus(baseUrl: string, token?: string): Observable<any> { return this.core.get(`${baseUrl}/tasks/autopilot/status`, baseUrl, token, true); }
  startAutopilot(baseUrl: string, body: { interval_seconds?: number; max_concurrency?: number; goal?: string; team_id?: string; budget_label?: string; security_level?: 'safe' | 'balanced' | 'aggressive'; }, token?: string): Observable<any> {
    return this.core.post(`${baseUrl}/tasks/autopilot/start`, body || {}, baseUrl, token);
  }
  stopAutopilot(baseUrl: string, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/autopilot/stop`, {}, baseUrl, token); }
  tickAutopilot(baseUrl: string, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/autopilot/tick`, {}, baseUrl, token); }

  getAutoPlannerStatus(baseUrl: string, token?: string): Observable<any> { return this.core.get(`${baseUrl}/tasks/auto-planner/status`, baseUrl, token, false); }
  configureAutoPlanner(baseUrl: string, config: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/tasks/auto-planner/configure`, config, baseUrl, token); }
  planGoal(baseUrl: string, body: { goal: string; context?: string; team_id?: string; parent_task_id?: string; create_tasks?: boolean }, token?: string): Observable<any> {
    return this.core.post(`${baseUrl}/tasks/auto-planner/plan`, body, baseUrl, token, false, 60000);
  }
  analyzeTaskForFollowups(baseUrl: string, taskId: string, body?: { output?: string; exit_code?: number }, token?: string): Observable<any> {
    return this.core.post(`${baseUrl}/tasks/auto-planner/analyze/${taskId}`, body || {}, baseUrl, token);
  }

  getTriggersStatus(baseUrl: string, token?: string): Observable<any> { return this.core.get(`${baseUrl}/triggers/status`, baseUrl, token, false); }
  configureTriggers(baseUrl: string, config: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/triggers/configure`, config, baseUrl, token); }
  testTrigger(baseUrl: string, body: { source: string; payload: any }, token?: string): Observable<any> { return this.core.post(`${baseUrl}/triggers/test`, body, baseUrl, token); }
}
