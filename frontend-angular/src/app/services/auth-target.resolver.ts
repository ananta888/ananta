/**
 * Explizites Zieltyp-Modell für den AuthInterceptor.
 *
 * Die bisherige Interceptor-Logik entschied anhand verstreuter If-Kaskaden zwischen
 * User-JWT, Agent-JWT (Shared Secret) und Fallback-Pfaden. Dieses Modul kapselt die
 * Zielauflösung in einen einzigen, mechanisch prüfbaren Resolver und benennt die
 * verbleibenden Fallbacks ausdrücklich, damit sicherheitsrelevante Entscheidungen
 * nachvollziehbar bleiben.
 */

import type { AgentEntry as Agent } from './agent-directory.service';

export type AuthTargetKind =
  /** Request geht an den Hub und wir haben einen gültigen User-Token – bevorzugter Weg. */
  | 'hub_user_bearer'
  /** Worker (oder Hub ohne User-Login) mit hinterlegtem Shared Secret – Agent-JWT wird erzeugt. */
  | 'agent_jwt_shared_secret'
  /** Weder Hub- noch Worker-URL erkannt – unverändert weiterreichen. */
  | 'passthrough_unknown_target'
  /** Zielagent bekannt, aber weder User-Token noch Shared Secret – unverändert weiterreichen. */
  | 'passthrough_no_credentials';

export interface AuthTarget {
  kind: AuthTargetKind;
  /** Der passende Agent aus dem Directory, falls die URL matcht. */
  agent: Agent | null;
  /**
   * Optionaler User-Bearer-Token, der direkt im Header landet.
   * Nur für `hub_user_bearer` belegt.
   */
  userToken: string | null;
  /**
   * Agent-Shared-Secret, aus dem ein kurzlebiger JWT gebaut wird.
   * Nur für `agent_jwt_shared_secret` belegt.
   */
  agentSharedSecret: string | null;
  /** Lesbarer Grund für Logs/Audit. */
  reason: string;
  /** Nur User-Bearer-Pfade duerfen 401->Refresh->Retry ausloesen. */
  refreshOnUnauthorized: boolean;
}

export interface AuthTargetContext {
  agents: Agent[];
  userToken: string | null;
  requestUrl: string;
}

export function normalizeAuthUrl(url: string): string {
  return String(url || '').trim().replace(/\/+$/, '');
}

export function resolveAgentForUrl(agents: Agent[], requestUrl: string): Agent | null {
  const normalizedRequestUrl = normalizeAuthUrl(requestUrl);
  const candidates = agents
    .filter(agent => normalizeAuthUrl(agent.url))
    .sort((left, right) => normalizeAuthUrl(right.url).length - normalizeAuthUrl(left.url).length);
  return candidates.find(agent => {
    const base = normalizeAuthUrl(agent.url);
    return normalizedRequestUrl === base || normalizedRequestUrl.startsWith(`${base}/`);
  }) ?? null;
}

export function resolveAuthTarget(ctx: AuthTargetContext): AuthTarget {
  const agent = resolveAgentForUrl(ctx.agents, ctx.requestUrl);

  if (!agent) {
    return {
      kind: 'passthrough_unknown_target',
      agent: null,
      userToken: null,
      agentSharedSecret: null,
      reason: 'Request-URL matcht keinen bekannten Agenten im Directory.',
      refreshOnUnauthorized: false,
    };
  }

  if (agent.role === 'hub' && ctx.userToken) {
    return {
      kind: 'hub_user_bearer',
      agent,
      userToken: ctx.userToken,
      agentSharedSecret: null,
      reason: 'Hub-Request mit gültigem User-JWT.',
      refreshOnUnauthorized: true,
    };
  }

  if (agent.token) {
    return {
      kind: 'agent_jwt_shared_secret',
      agent,
      userToken: null,
      agentSharedSecret: agent.token,
      reason: 'Agent hat Shared Secret hinterlegt – kurzlebiger Agent-JWT wird erzeugt.',
      refreshOnUnauthorized: false,
    };
  }

  // Default-deny: kein Shared-Secret, kein User-Token für Worker-Endpoints.
  // Wir leiten den Request NICHT mit dem User-Token an einen Worker weiter,
  // weil der Worker per `@check_user_auth` einen User-JWT des Hubs erwartet
  // und der User-JWT des Workers in einer anderen Auth-Sphäre lebt.
  // Stattdessen geben wir passthrough zurück; das Frontend zeigt dann die
  // passende Login-Maske (siehe docs/identity-architecture.md).
  return {
    kind: 'passthrough_no_credentials',
    agent,
    userToken: null,
    agentSharedSecret: null,
    reason:
      'Worker ohne Shared Secret – kein impliziter User-Token-Fallback ' +
      '(default-deny). Frontend zeigt Login-Maske.',
    refreshOnUnauthorized: false,
  };
}
