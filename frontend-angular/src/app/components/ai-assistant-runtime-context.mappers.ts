import { AssistantRuntimeContext } from './ai-assistant.types';

/**
 * Pure mappers that turn hub read-model / legacy config payloads into the
 * compact assistant runtime context sent with every assistant request.
 * Kept free of Angular state so they can be tested and reused in isolation.
 */

export type AssistantAgentDescriptor = { name: string; role?: string; url: string };

/** Normalizes raw agent rows and drops entries without name or url. */
export function toAgentDescriptors(items: any[]): AssistantAgentDescriptor[] {
  return items
    .map((a: any) => ({ name: String(a?.name || ''), role: a?.role, url: String(a?.url || '') }))
    .filter((a: any) => a.name && a.url);
}

export function toCompactConfigSnapshot(cfg: any) {
  if (!cfg || typeof cfg !== 'object') return null;
  return {
    default_provider: cfg.default_provider || null,
    default_model: cfg.default_model || null,
    template_agent_name: cfg.template_agent_name || null,
    team_agent_name: cfg.team_agent_name || null,
    sgpt_execution_backend: cfg.sgpt_execution_backend || null,
    llm_config: cfg.llm_config ? {
      provider: cfg.llm_config.provider || null,
      model: cfg.llm_config.model || null,
      lmstudio_api_mode: cfg.llm_config.lmstudio_api_mode || null,
    } : null,
    codex_cli: cfg.codex_cli ? {
      base_url: cfg.codex_cli.base_url || null,
      api_key_profile: cfg.codex_cli.api_key_profile || null,
      prefer_lmstudio: cfg.codex_cli.prefer_lmstudio ?? null,
      auth_mode: cfg.codex_cli.auth_mode || null,
    } : null,
    claude_cli: cfg.claude_cli ? {
      enabled: cfg.claude_cli.enabled ?? null,
      auth_mode: cfg.claude_cli.auth_mode || null,
      permission_mode: cfg.claude_cli.permission_mode || null,
      default_model: cfg.claude_cli.default_model || null,
    } : null,
  };
}

export function toTemplateSummary(templates: any[]): Array<{ name: string; description?: string }> {
  if (!Array.isArray(templates)) return [];
  const maxTemplates = 25;
  const maxDescriptionChars = 180;

  return templates
    .flatMap((tpl: any) => {
      const name = String(tpl?.name || '').trim();
      if (!name) return [];
      const rawDescription = String(tpl?.description || '').replace(/\s+/g, ' ').trim();
      const description = rawDescription ? rawDescription.slice(0, maxDescriptionChars) : undefined;
      return [description ? { name, description } : { name }];
    })
    .slice(0, maxTemplates);
}

export function toEditableSettingsSummary(items: any[]): Array<{ key: string; path?: string; type?: string; endpoint?: string }> {
  if (!Array.isArray(items)) return [];
  return items
    .map((item: any) => ({
      key: String(item?.key || '').trim(),
      path: item?.path ? String(item.path) : undefined,
      type: item?.type ? String(item.type) : undefined,
      endpoint: item?.endpoint ? String(item.endpoint) : undefined,
    }))
    .filter((item) => !!item.key)
    .slice(0, 60);
}

export function toLegacySettingsSummary(cfg: any) {
  if (!cfg || typeof cfg !== 'object') return null;
  return {
    llm: {
      default_provider: cfg.default_provider || null,
      default_model: cfg.default_model || null,
    },
    system: {
      log_level: cfg.log_level || null,
      http_timeout: cfg.http_timeout ?? null,
      command_timeout: cfg.command_timeout ?? null,
    },
  };
}

/** Maps the unified assistant read model onto the runtime context. */
export function contextFromReadModel(
  baseCtx: AssistantRuntimeContext,
  res: any,
  fallbackAgents: AssistantAgentDescriptor[],
): AssistantRuntimeContext {
  const teams = Array.isArray(res?.teams?.items) ? res.teams.items : [];
  const templates = Array.isArray(res?.templates?.items) ? res.templates.items : [];
  const effectiveAgents = Array.isArray(res?.agents?.items)
    ? toAgentDescriptors(res.agents.items)
    : (Array.isArray(res?.agents) ? res.agents : []);
  const mappedAgents = Array.isArray(effectiveAgents) && effectiveAgents.length ? effectiveAgents : fallbackAgents;
  return {
    ...baseCtx,
    agents: mappedAgents,
    teamsCount: teams.length,
    templatesCount: templates.length,
    templatesSummary: toTemplateSummary(templates),
    settingsSummary: res?.settings?.summary || null,
    editableSettings: toEditableSettingsSummary(res?.settings?.editable_inventory),
    automationSummary: res?.automation || null,
    hasConfig: !!res?.config?.effective,
    configSnapshot: toCompactConfigSnapshot(res?.config?.effective || {}),
  };
}

/** Maps the legacy per-endpoint responses (config/teams/templates/agents). */
export function contextFromLegacyResponses(
  baseCtx: AssistantRuntimeContext,
  legacyRes: { config: any; teams: any; templates: any; agents: any },
  fallbackAgents: AssistantAgentDescriptor[],
): AssistantRuntimeContext {
  const teams = Array.isArray(legacyRes.teams) ? legacyRes.teams : [];
  const templates = Array.isArray(legacyRes.templates) ? legacyRes.templates : [];
  const effectiveAgents = Array.isArray(legacyRes.agents)
    ? toAgentDescriptors(legacyRes.agents)
    : fallbackAgents;
  return {
    ...baseCtx,
    agents: effectiveAgents,
    teamsCount: teams.length,
    templatesCount: templates.length,
    templatesSummary: toTemplateSummary(templates),
    settingsSummary: toLegacySettingsSummary(legacyRes.config),
    editableSettings: [],
    automationSummary: null,
    hasConfig: !!legacyRes.config,
    configSnapshot: toCompactConfigSnapshot(legacyRes.config),
  };
}

/** Wire shape of the context attached to assistant LLM requests. */
export function toAssistantRequestContext(ctx: AssistantRuntimeContext) {
  return {
    route: ctx.route,
    selected_agent: ctx.selectedAgentName || null,
    user: {
      name: ctx.userName || null,
      role: ctx.userRole || null,
    },
    agents: ctx.agents,
    teams_count: ctx.teamsCount,
    templates_count: ctx.templatesCount,
    templates_summary: ctx.templatesSummary,
    settings_summary: ctx.settingsSummary || null,
    editable_settings: ctx.editableSettings,
    automation_summary: ctx.automationSummary || null,
    has_config: ctx.hasConfig,
    config_snapshot: ctx.configSnapshot || null,
  };
}
