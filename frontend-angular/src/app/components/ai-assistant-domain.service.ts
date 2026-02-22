import { Injectable } from '@angular/core';
import { ChatMessage } from './ai-assistant.types';

@Injectable({ providedIn: 'root' })
export class AiAssistantDomainService {
  formatToolName(name?: string): string {
    const n = (name || '').trim();
    if (!n) return 'Unknown tool';
    return n.split('_').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
  }

  summarizeToolScope(tc: any): string {
    const name = String(tc?.name || '');
    const args = tc?.args || {};
    if (name === 'ensure_team_templates') {
      const teamTypes = Array.isArray(args.team_types) ? args.team_types.filter(Boolean) : [];
      return teamTypes.length ? `Team types: ${teamTypes.join(', ')}` : 'Default team types';
    }
    if (name === 'create_template') return args?.name ? `Template: ${args.name}` : 'New template';
    if (name === 'update_template') return args?.template_id ? `Template ID: ${args.template_id}` : 'Existing template';
    if (name === 'delete_template') return args?.template_id ? `Template ID: ${args.template_id}` : 'Template';
    if (name === 'create_team') return `${args?.name || 'New team'} (${args?.team_type || 'unknown type'})`;
    if (name === 'upsert_team') return args?.team_id ? `Team ID: ${args.team_id}` : `Team: ${args?.name || 'new'}`;
    if (name === 'upsert_role') return args?.role_id ? `Role ID: ${args.role_id}` : `Role: ${args?.name || 'new'}`;
    if (name === 'upsert_team_type') return args?.type_id ? `Type ID: ${args.type_id}` : `Type: ${args?.name || 'new'}`;
    if (name === 'set_autopilot_state') return `Autopilot action: ${args?.action || 'unknown'}`;
    return 'See raw args';
  }

  summarizeToolImpact(tc: any): string {
    const name = String(tc?.name || '');
    if (name === 'ensure_team_templates') return 'Ensure default templates and role links exist.';
    if (name === 'create_template') return 'Create a new prompt template.';
    if (name === 'update_template') return 'Update an existing prompt template.';
    if (name === 'delete_template') return 'Delete a prompt template.';
    if (name === 'create_team') return 'Create a team and prepare defaults.';
    if (name === 'upsert_team' || name === 'delete_team' || name === 'activate_team') return 'Mutate team configuration.';
    if (name === 'upsert_role' || name === 'delete_role') return 'Mutate role configuration.';
    if (name === 'upsert_team_type' || name === 'delete_team_type') return 'Mutate team type configuration.';
    if (name === 'link_role_to_team_type' || name === 'unlink_role_from_team_type' || name === 'set_role_template_mapping') return 'Mutate role-template mapping.';
    if (name === 'configure_auto_planner') return 'Update auto-planner configuration.';
    if (name === 'configure_triggers') return 'Update triggers/webhook configuration.';
    if (name === 'set_autopilot_state') return 'Control autopilot runtime state.';
    return 'Executes an admin action.';
  }

  summarizeToolChanges(tc: any): string {
    const name = String(tc?.name || '');
    const args = tc?.args || {};
    if (name === 'update_config') return `config.${args?.key || 'key'} => ${JSON.stringify(args?.value ?? null)}`;
    if (name === 'create_template') return `create template '${args?.name || 'unnamed'}'`;
    if (name === 'update_template') return `update template '${args?.template_id || 'unknown'}'`;
    if (name === 'delete_template') return `delete template '${args?.template_id || 'unknown'}'`;
    if (name === 'create_team') return `create team '${args?.name || 'unnamed'}'`;
    if (name === 'assign_role') return `assign role '${args?.role_id || 'unknown'}' to '${args?.agent_url || 'agent'}'`;
    if (name === 'ensure_team_templates') return `ensure defaults for ${(args?.team_types || []).join(', ') || 'Scrum/Kanban'}`;
    if (name === 'upsert_team') return `${args?.team_id ? 'update' : 'create'} team '${args?.name || 'unnamed'}'`;
    if (name === 'delete_team') return `delete team '${args?.team_id || 'unknown'}'`;
    if (name === 'activate_team') return `activate team '${args?.team_id || 'unknown'}'`;
    if (name === 'upsert_team_type') return `${args?.type_id ? 'update' : 'create'} team type '${args?.name || 'unnamed'}'`;
    if (name === 'delete_team_type') return `delete team type '${args?.type_id || 'unknown'}'`;
    if (name === 'upsert_role') return `${args?.role_id ? 'update' : 'create'} role '${args?.name || 'unnamed'}'`;
    if (name === 'delete_role') return `delete role '${args?.role_id || 'unknown'}'`;
    if (name === 'link_role_to_team_type') return `link role '${args?.role_id || 'unknown'}' to team type '${args?.type_id || 'unknown'}'`;
    if (name === 'unlink_role_from_team_type') return `unlink role '${args?.role_id || 'unknown'}' from team type '${args?.type_id || 'unknown'}'`;
    if (name === 'set_role_template_mapping') return `set role-template mapping for '${args?.role_id || 'unknown'}'`;
    if (name === 'configure_auto_planner') return 'update auto-planner settings';
    if (name === 'configure_triggers') return 'update trigger settings';
    if (name === 'set_autopilot_state') return `autopilot => ${args?.action || 'unknown'}`;
    return 'See raw args for exact changes.';
  }

  assessPlanRisk(toolCalls: any[]): { level: 'low' | 'medium' | 'high'; reason: string } {
    const names = (Array.isArray(toolCalls) ? toolCalls : []).map(tc => String(tc?.name || '').toLowerCase());
    if (names.some(n => n.includes('delete') || n.includes('update_config') || n.includes('assign_role'))) {
      return { level: 'high', reason: 'Includes destructive or privilege-changing actions.' };
    }
    if (names.some(n => n.includes('create') || n.includes('update'))) {
      return { level: 'medium', reason: 'Includes mutating actions.' };
    }
    return { level: 'low', reason: 'Read-only or low-impact action set.' };
  }

  quickActions(route: string): Array<{ label: string; prompt: string }> {
    if (route.startsWith('/teams')) {
      return [
        { label: 'Team Check', prompt: 'Pruefe Team-Konfiguration und gib konkrete Verbesserungen aus.' },
        { label: 'Role Check', prompt: 'Pruefe Rollen- und Template-Zuordnungen auf Luecken.' },
      ];
    }
    if (route.startsWith('/templates')) {
      return [
        { label: 'Template Audit', prompt: 'Analysiere vorhandene Templates und markiere Duplikate/Luecken.' },
        { label: 'Naming Cleanup', prompt: 'Schlage ein konsistentes Namensschema fuer Templates vor.' },
      ];
    }
    return [
      { label: 'Health Summary', prompt: 'Erstelle eine kurze Systemzusammenfassung mit Prioritaeten.' },
      { label: 'Next Steps', prompt: 'Schlage die naechsten 3 operativen Schritte fuer dieses Projekt vor.' },
    ];
  }

  extractSgptCommand(content: string): string | undefined {
    const shellMatch = content.match(/```(?:bash|sh|shell)?\n([\s\S]+?)\n```/);
    if (!shellMatch?.[1]) return undefined;
    const potentialCmd = shellMatch[1].trim();
    if (!potentialCmd || potentialCmd.length >= 200 || potentialCmd.includes('\n')) return undefined;
    return potentialCmd;
  }

  persistHistory(storageKey: string, chatHistory: ChatMessage[]) {
    try {
      const payload = chatHistory.slice(-40).map(m => ({ role: m.role, content: m.content }));
      localStorage.setItem(storageKey, JSON.stringify(payload));
    } catch {}
  }

  restoreHistory(storageKey: string): ChatMessage[] {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed
        .filter((m: any) => (m?.role === 'user' || m?.role === 'assistant') && typeof m?.content === 'string')
        .slice(-40);
    } catch {
      return [];
    }
  }
}
