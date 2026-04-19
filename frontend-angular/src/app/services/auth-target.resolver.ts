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
  /** Ausdrücklicher Fallback: Worker ohne Agent-Token, aber vorhandener User-Token. */
  | 'user_bearer_fallback_on_worker'
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
   * Nur für `hub_user_bearer` und `user_bearer_fallback_on_worker` belegt.
   */
  userToken: string | null;
  /**
   * Agent-Shared-Secret, aus dem ein kurzlebiger JWT gebaut wird.
   * Nur für `agent_jwt_shared_secret` belegt.
   */
  agentSharedSecret: string | null;
  /** Lesbarer Grund für Logs/Audit. */
  reason: string;
}

export interface AuthTargetContext {
  agents: Agent[];
  userToken: string | null;
  requestUrl: string;
}

export function resolveAuthTarget(ctx: AuthTargetContext): AuthTarget {
  const agent = ctx.agents.find(a => ctx.requestUrl.startsWith(a.url)) ?? null;

  if (!agent) {
    return {
      kind: 'passthrough_unknown_target',
      agent: null,
      userToken: null,
      agentSharedSecret: null,
      reason: 'Request-URL matcht keinen bekannten Agenten im Directory.',
    };
  }

  if (agent.role === 'hub' && ctx.userToken) {
    return {
      kind: 'hub_user_bearer',
      agent,
      userToken: ctx.userToken,
      agentSharedSecret: null,
      reason: 'Hub-Request mit gültigem User-JWT.',
    };
  }

  if (agent.token) {
    return {
      kind: 'agent_jwt_shared_secret',
      agent,
      userToken: null,
      agentSharedSecret: agent.token,
      reason: 'Agent hat Shared Secret hinterlegt – kurzlebiger Agent-JWT wird erzeugt.',
    };
  }

  if (ctx.userToken) {
    return {
      kind: 'user_bearer_fallback_on_worker',
      agent,
      userToken: ctx.userToken,
      agentSharedSecret: null,
      reason:
        'Worker ohne Shared Secret, aber gültiger User-Token vorhanden – ' +
        'Fallback verwendet, um 401->refresh->retry-Zyklen bei read-only Requests zu vermeiden.',
    };
  }

  return {
    kind: 'passthrough_no_credentials',
    agent,
    userToken: null,
    agentSharedSecret: null,
    reason: 'Zielagent erkannt, aber weder User-Token noch Shared Secret verfügbar.',
  };
}
