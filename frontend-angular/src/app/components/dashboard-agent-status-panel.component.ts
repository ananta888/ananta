import { CommonModule } from '@angular/common';
import { Component, Input } from '@angular/core';

import { AgentEntry } from '../models/dashboard.models';

@Component({
  standalone: true,
  selector: 'app-dashboard-agent-status-panel',
  imports: [CommonModule],
  template: `
    <div class="card">
      <h3>Agenten Status</h3>
      <div class="grid cols-4">
        @for (agent of agents; track agent) {
          <div class="agent-card">
            <div class="row gap-sm">
              <div
                class="status-dot"
                [class.online]="agent.status === 'online'"
                [class.offline]="agent.status !== 'online'"
                role="status"
                [attr.aria-label]="agent.name + ' ist ' + (agent.status === 'online' ? 'online' : 'offline')"
              ></div>
              <span class="font-weight-medium">{{ agent.name }}</span>
              <span class="muted font-sm">{{ agent.role }}</span>
            </div>
            <div class="muted font-sm mt-sm row space-between">
              <span>Routing: {{ agentRoutingState(agent) }}</span>
              <span>Load: {{ agentCurrentLoad(agent) }}</span>
            </div>
            @if (agent?.liveness) {
              <div class="muted font-sm mt-sm">
                Last seen: {{ agentLastSeen(agent) }}
                @if (agent?.liveness?.stale_seconds !== undefined && agent?.liveness?.stale_seconds !== null) {
                  <span> · stale {{ agent.liveness.stale_seconds }}s</span>
                }
              </div>
            }
            @if (agent?.security_level) {
              <div class="muted font-sm mt-sm">Security: {{ agent.security_level }}</div>
            }
            @if (agent.resources) {
              <div class="muted font-sm mt-sm row space-between">
                <span>CPU: {{ agent.resources.cpu_percent | number:'1.0-1' }}%</span>
                <span>RAM: {{ agent.resources.ram_bytes / 1024 / 1024 | number:'1.0-0' }} MB</span>
              </div>
            }
          </div>
        }
      </div>
    </div>
  `,
})
export class DashboardAgentStatusPanelComponent {
  @Input() agents: AgentEntry[] = [];

  agentRoutingState(agent: any): string {
    const available = agent?.liveness?.available_for_routing;
    if (available === false && agent?.status === 'online') return 'paused';
    if (available === true) return 'ready';
    return String(agent?.liveness?.status || agent?.status || 'unknown');
  }

  agentCurrentLoad(agent: any): number {
    return Number(agent?.current_load ?? agent?.routing_signals?.current_load ?? 0);
  }

  agentLastSeen(agent: any): string {
    const lastSeen = Number(agent?.liveness?.last_seen || 0);
    if (!lastSeen) return '-';
    return new Date(lastSeen * 1000).toLocaleTimeString();
  }
}
