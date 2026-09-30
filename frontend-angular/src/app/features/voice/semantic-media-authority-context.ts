import { NetworkProfile } from '../../services/network-profile.service';
import { PairSessionControlPlaneService } from '../../services/pair-session-control-plane.service';
import { ActiveShareState, ShareSession } from '../../services/share-session.service';
import { OrdinaryMediaAuthorityKind } from './semantic-media-program-shell.component';
import {
  BoundSemanticMediaAuthorityRoute,
  SpeechActivationFence,
  sessionUsable,
} from './semantic-media-program.models';

/**
 * Current session/profile/online snapshot of the semantic media program and
 * the single router between Public and Hub media authority. Collaborators
 * read authority decisions from here instead of re-deriving them.
 */
export class SemanticMediaAuthorityContext {
  /** Advances whenever the bound session id or security epoch changes. */
  private contextGenerationValue = 0;

  constructor(
    private readonly controlPlane: Pick<PairSessionControlPlaneService, 'authorityRouteForSession'>,
    public shareState: ActiveShareState,
    public profile: NetworkProfile,
    public runtimeOnline: boolean,
  ) {}

  get session(): ShareSession | null { return this.shareState.session; }

  get contextGeneration(): number { return this.contextGenerationValue; }

  advanceContextGeneration(): void { this.contextGenerationValue += 1; }

  /** Exact session binding is the sole router between Public and Hub media operations. */
  route(): BoundSemanticMediaAuthorityRoute {
    const sessionId = this.shareState.session?.id ?? '';
    if (!sessionId) return Object.freeze({ sessionId: '', kind: 'unbound', baseUrl: '' });
    try {
      const route = this.controlPlane.authorityRouteForSession(sessionId);
      const baseUrl = String(route.baseUrl || '').trim().replace(/\/+$/, '');
      if (route.kind === 'hub' && !baseUrl) throw new Error('semantic_program_hub_missing');
      return Object.freeze({
        sessionId,
        kind: route.kind,
        baseUrl: route.kind === 'hub' ? baseUrl : '',
      });
    } catch {
      return Object.freeze({ sessionId, kind: 'unbound', baseUrl: '' });
    }
  }

  kind(): OrdinaryMediaAuthorityKind { return this.route().kind; }

  hasHubAuthority(): boolean { return this.kind() === 'hub'; }

  hubUrl(): string {
    const authority = this.route();
    return authority.kind === 'hub' ? authority.baseUrl : '';
  }

  hubOnline(): boolean { return Boolean(this.hubUrl()) && this.runtimeOnline; }

  hubOperationsAvailable(): boolean { return this.hasHubAuthority() && this.hubOnline(); }

  hubOperationUnavailableReason(): string {
    return this.hasHubAuthority()
      ? 'semantic_program_hub_offline'
      : 'semantic_program_hub_authority_required';
  }

  requireSession(): ShareSession {
    const session = this.shareState.session;
    if (!sessionUsable(session)) throw new Error('semantic_program_session_missing');
    return session;
  }

  requireHubOperationAuthority(): void {
    this.requireSession();
    if (!this.hasHubAuthority()) throw new Error('semantic_program_hub_authority_required');
  }

  /** Binds a speech activation attempt to the exact session, epoch and Hub route it started on. */
  speechActivationFence(session: ShareSession, activationGeneration: number): SpeechActivationFence {
    const authority = this.route();
    if (authority.kind !== 'hub' || !authority.baseUrl) {
      throw new Error('semantic_program_hub_authority_required');
    }
    return Object.freeze({
      sessionId: session.id,
      securityEpoch: session.security_epoch ?? 0,
      authorityBaseUrl: authority.baseUrl,
      contextGeneration: this.contextGenerationValue,
      activationGeneration,
    });
  }

  assertSpeechActivationFence(fence: SpeechActivationFence, currentActivationGeneration: number): void {
    const session = this.shareState.session;
    const authority = this.route();
    if (
      fence.activationGeneration !== currentActivationGeneration
      || fence.contextGeneration !== this.contextGenerationValue
      || session?.id !== fence.sessionId
      || (session.security_epoch ?? 0) !== fence.securityEpoch
      || authority.kind !== 'hub'
      || authority.baseUrl !== fence.authorityBaseUrl
    ) throw new Error('semantic_speech_activation_stale');
  }
}
