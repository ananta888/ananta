import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { LlmApiClient, LlmGenerateOptions } from './llm-api.client';
import { SgptApiClient, SgptBackend } from './sgpt-api.client';
import { SystemApiClient } from './system-api.client';
import { TaskApiClient } from './task-api.client';
import { WorkspaceApiClient } from './workspace-api.client';

/**
 * Fassade über die fachlichen API-Clients.
 *
 * Die eigentliche Endpoint-Logik lebt in System/Task/Llm/Sgpt/Workspace-Clients.
 * Diese Klasse bleibt als Aggregator erhalten, damit bestehende Call-Sites
 * (AgentPanel, AiAssistant, Artifacts, Settings, SystemFacade) unverändert bleiben.
 */
@Injectable({ providedIn: 'root' })
export class AgentApiService {
  private system = inject(SystemApiClient);
  private tasks = inject(TaskApiClient);
  private llm = inject(LlmApiClient);
  private sgpt = inject(SgptApiClient);
  private workspace = inject(WorkspaceApiClient);

  health(baseUrl: string, token?: string): Observable<any> {
    return this.system.health(baseUrl, token);
  }
  ready(baseUrl: string, token?: string): Observable<any> {
    return this.system.ready(baseUrl, token);
  }
  getConfig(baseUrl: string, token?: string): Observable<any> {
    return this.system.getConfig(baseUrl, token);
  }
  getEvolutionProviders(baseUrl: string, token?: string): Observable<any> {
    return this.system.getEvolutionProviders(baseUrl, token);
  }
  listAgents(baseUrl: string, token?: string): Observable<any> {
    return this.system.listAgents(baseUrl, token);
  }
  probeAgent(baseUrl: string, workerName: string, token?: string): Observable<any> {
    return this.system.probeAgent(baseUrl, workerName, token);
  }
  setConfig(baseUrl: string, cfg: any, token?: string): Observable<any> {
    return this.system.setConfig(baseUrl, cfg, token);
  }
  propose(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.tasks.propose(baseUrl, body, token);
  }
  execute(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.tasks.execute(baseUrl, body, token);
  }
  logs(baseUrl: string, limit = 200, taskId?: string, token?: string): Observable<any> {
    return this.system.logs(baseUrl, limit, taskId, token);
  }
  rotateToken(baseUrl: string, token?: string): Observable<any> {
    return this.system.rotateToken(baseUrl, token);
  }
  getMetrics(baseUrl: string, token?: string): Observable<string> {
    return this.system.getMetrics(baseUrl, token);
  }
  getClassroomCards(baseUrl: string, filters: Record<string, string> = {}, token?: string): Observable<any> {
    return this.system.getClassroomCards(baseUrl, filters, token);
  }
  updateClassroomCard(baseUrl: string, cardId: string, status: string, token?: string): Observable<any> {
    return this.system.updateClassroomCard(baseUrl, cardId, status, token);
  }
  exportClassroomWorkflow(baseUrl: string, cardId: string, token?: string): Observable<any> {
    return this.system.exportClassroomWorkflow(baseUrl, cardId, token);
  }
  llmGenerate(
    baseUrl: string,
    prompt: string,
    config?: any,
    token?: string,
    options?: LlmGenerateOptions,
  ): Observable<any> {
    return this.llm.generate(baseUrl, prompt, config, token, options);
  }
  sgptExecute(
    baseUrl: string,
    prompt: string,
    options: string[] = [],
    token?: string,
    useHybridContext = false,
    backend?: SgptBackend,
  ): Observable<any> {
    return this.sgpt.execute(baseUrl, prompt, options, token, useHybridContext, backend);
  }
  sgptContext(baseUrl: string, query: string, token?: string, includeContextText = true): Observable<any> {
    return this.sgpt.context(baseUrl, query, token, includeContextText);
  }
  sgptSource(baseUrl: string, sourcePath: string, token?: string): Observable<any> {
    return this.sgpt.source(baseUrl, sourcePath, token);
  }
  sgptBackends(baseUrl: string, token?: string): Observable<any> {
    return this.sgpt.backends(baseUrl, token);
  }
  sgptBackendHealth(baseUrl: string, backendId: string, token?: string): Observable<any> {
    return this.sgpt.backendHealth(baseUrl, backendId, token);
  }
  sgptBackendDiagnose(baseUrl: string, backendId: string, token?: string): Observable<any> {
    return this.sgpt.backendDiagnose(baseUrl, backendId, token);
  }
  sgptBackendProvision(
    baseUrl: string,
    backendId: string,
    body: { worker_url: string; action: 'status' | 'install' },
    token?: string,
  ): Observable<any> {
    return this.sgpt.backendProvision(baseUrl, backendId, body, token);
  }
  sgptBackendWorkerAction(
    baseUrl: string,
    backendId: string,
    body: {
      worker_name: string;
      action:
        | 'diagnose'
        | 'test_run'
        | 'account_status'
        | 'login_start'
        | 'login_status'
        | 'login_input'
        | 'login_cancel';
      prompt?: string;
      model?: string;
      timeout?: number;
      session_id?: string;
      value?: string;
    },
    token?: string,
  ): Observable<any> {
    return this.sgpt.backendWorkerAction(baseUrl, backendId, body, token);
  }
  sgptBackendTestRun(baseUrl: string, backendId: string, body: { prompt?: string; model?: string; timeout?: number } = {}, token?: string): Observable<any> {
    return this.sgpt.backendTestRun(baseUrl, backendId, body, token);
  }
  claudeWriteArmedRun(baseUrl: string, body: { prompt: string; workdir: string; model?: string; timeout?: number }, token?: string): Observable<any> {
    return this.sgpt.claudeWriteArmedRun(baseUrl, body, token);
  }
  claudeApplyDiff(baseUrl: string, body: { diff: string; workdir: string }, token?: string): Observable<any> {
    return this.sgpt.claudeApplyDiff(baseUrl, body, token);
  }
  getLlmHistory(baseUrl: string, token?: string): Observable<any> {
    return this.llm.history(baseUrl, token);
  }
  taskWorkspaceFiles(
    baseUrl: string,
    taskId: string,
    token?: string,
    options?: { trackedOnly?: boolean; maxEntries?: number },
  ): Observable<any> {
    return this.workspace.taskWorkspaceFiles(baseUrl, taskId, token, options);
  }
}
