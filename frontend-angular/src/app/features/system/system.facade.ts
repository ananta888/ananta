import { Injectable, inject } from '@angular/core';

import { AgentApiService } from '../../services/agent-api.service';
import { AgentDirectoryService, AgentEntry } from '../../services/agent-directory.service';
import { HubApiService } from '../../services/hub-api.service';
import { HubLiveStateService } from '../../services/hub-live-state.service';
import { SystemAgentStatusStateService } from './system-agent-status-state.service';

@Injectable({ providedIn: 'root' })
export class SystemFacade {
  private agentApi = inject(AgentApiService);
  private agentDirectory = inject(AgentDirectoryService);
  private hubApi = inject(HubApiService);
  private liveState = inject(HubLiveStateService);
  private agentStatusState = inject(SystemAgentStatusStateService);

  listConfiguredAgents(): AgentEntry[] {
    return this.agentDirectory.list();
  }

  resolveHubAgent(): AgentEntry | undefined {
    return this.listConfiguredAgents().find((agent) => agent.role === 'hub');
  }

  upsertConfiguredAgent(entry: AgentEntry): void {
    this.agentDirectory.upsert(entry);
  }

  removeConfiguredAgent(name: string): void {
    this.agentDirectory.remove(name);
  }

  connectAgentStatuses(hubUrl?: string | null, pollMs?: number): void {
    this.agentStatusState.connect(hubUrl || this.resolveHubAgent()?.url, pollMs);
  }

  disconnectAgentStatuses(hubUrl?: string | null): void {
    this.agentStatusState.disconnect(hubUrl || this.resolveHubAgent()?.url);
  }

  reloadAgentStatuses(): void {
    this.agentStatusState.reload();
  }

  agentStatusesLoading(): boolean {
    return this.agentStatusState.loading();
  }

  agentStatusesLastLoadedAt(): number | null {
    return this.agentStatusState.lastLoadedAt();
  }

  agentStatusError(): string | null {
    return this.agentStatusState.error();
  }

  agentStatus(agentName: string): string | null {
    return this.agentStatusState.statusFor(agentName);
  }

  agentStatusSummary(agentNames: string[]): { online: number; offline: number; unknown: number } {
    return agentNames.reduce((acc, agentName) => {
      const status = String(this.agentStatus(agentName) || 'unknown').toLowerCase();
      if (status === 'online') acc.online += 1;
      else if (status === 'offline') acc.offline += 1;
      else acc.unknown += 1;
      return acc;
    }, { online: 0, offline: 0, unknown: 0 });
  }

  ensureSystemEvents(hubUrl?: string | null): void {
    this.liveState.ensureSystemEvents(hubUrl || this.resolveHubAgent()?.url);
  }

  disconnectSystemEvents(): void {
    this.liveState.disconnectSystemEvents();
  }

  systemStreamConnected(): boolean {
    return this.liveState.systemStreamConnected();
  }

  lastSystemEvent(): any | null {
    return this.liveState.lastSystemEvent();
  }

  liveSnapshot(): { systemStreamConnected: boolean; lastSystemEvent: any | null; activeTaskLogStreams: number } {
    return this.liveState.snapshot();
  }

  health(baseUrl: string, token?: string) {
    return this.agentApi.health(baseUrl, token);
  }

  ready(baseUrl: string, token?: string) {
    return this.agentApi.ready(baseUrl, token);
  }

  getConfig(baseUrl: string, token?: string) {
    return this.agentApi.getConfig(baseUrl, token);
  }

  setConfig(baseUrl: string, payload: any, token?: string) {
    return this.agentApi.setConfig(baseUrl, payload, token);
  }

  getLlmHistory(baseUrl: string, token?: string) {
    return this.agentApi.getLlmHistory(baseUrl, token);
  }

  listProviderCatalog(baseUrl: string, token?: string) {
    return this.hubApi.listProviderCatalog(baseUrl, token);
  }

  getLlmBenchmarksConfig(baseUrl: string, token?: string) {
    return this.hubApi.getLlmBenchmarksConfig(baseUrl, token);
  }

  getAuditLogs(baseUrl: string, limit = 100, offset = 0, token?: string) {
    return this.hubApi.getAuditLogs(baseUrl, limit, offset, token);
  }

  analyzeAuditLogs(baseUrl: string, limit = 50, token?: string) {
    return this.hubApi.analyzeAuditLogs(baseUrl, limit, token);
  }

  getTriggersStatus(baseUrl: string, token?: string) {
    return this.hubApi.getTriggersStatus(baseUrl, token);
  }

  configureTriggers(baseUrl: string, payload: any, token?: string) {
    return this.hubApi.configureTriggers(baseUrl, payload, token);
  }

  testTrigger(baseUrl: string, payload: { source: string; payload: any }, token?: string) {
    return this.hubApi.testTrigger(baseUrl, payload, token);
  }
}
