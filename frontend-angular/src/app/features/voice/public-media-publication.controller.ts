import { WebrtcMediaSessionService } from '../../services/webrtc-media-session.service';
import { WebrtcMediaPublicationService } from '../../services/webrtc-media-publication.service';
import {
  PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE,
  PairOrdinaryMediaPolicy,
} from '../../services/pair-ordinary-media.policy';
import {
  PairMediaE2eeCoordinatorService,
  type PublicPairMediaE2eeState,
  type PublicPairMediaPublicationContext,
} from '../../services/pair-media-e2ee-coordinator.service';
import {
  PublicPairMediaPublicationConsentService,
  type PublicPairMediaPublicationConsentState,
  type PublicPairMediaPublicationConsentTerm,
} from '../../services/public-pair-media-publication-consent.service';
import { ShareSession } from '../../services/share-session.service';
import { SemanticMediaAuthorityContext } from './semantic-media-authority-context';
import {
  SemanticProgramCapabilityView,
  SemanticProgramState,
} from './semantic-media-program-shell.component';
import { SetSemanticCapability, reason } from './semantic-media-program.models';

export interface PublicMediaPublicationHost {
  readonly emit: () => void;
  readonly setCapability: SetSemanticCapability;
  readonly ordinaryMediaState: () => SemanticProgramCapabilityView | undefined;
  readonly setOperationReason: (reasonCode: string) => void;
}

export interface PublicMediaPublicationDeps {
  readonly publicationConsent: PublicPairMediaPublicationConsentService;
  readonly pairMediaE2ee: PairMediaE2eeCoordinatorService;
  readonly ordinaryMediaPolicy: PairOrdinaryMediaPolicy;
  readonly media: WebrtcMediaSessionService;
  readonly mediaPublications: WebrtcMediaPublicationService;
}

interface PublicationConsentPreparationFence {
  readonly sessionId: string;
  readonly securityEpoch: number;
  readonly contextGeneration: number;
  readonly preparationGeneration: number;
}

export function ordinaryMediaPolicyReason(policy: PairOrdinaryMediaPolicy, sessionId: string): string | null {
  try {
    policy.assertAllowed(sessionId);
    return null;
  } catch (error) {
    return reason(error, PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE);
  }
}

/**
 * Public (non-Hub) Pair ordinary media: publication consent preparation,
 * grant/revoke, E2EE readiness tracking and the projection of both onto the
 * ordinary_media capability. Hub-routed media never passes through here.
 */
export class PublicMediaPublicationController {
  private consentStateValue: PublicPairMediaPublicationConsentState;
  private preparationGeneration = 0;
  private preparationPendingValue = false;
  private readySessionId = '';
  private failureCleanupSessionId = '';

  constructor(
    private readonly deps: PublicMediaPublicationDeps,
    private readonly authority: SemanticMediaAuthorityContext,
    private readonly host: PublicMediaPublicationHost,
  ) {
    this.consentStateValue = deps.publicationConsent.snapshot();
  }

  get consentState(): PublicPairMediaPublicationConsentState { return this.consentStateValue; }

  get preparationPending(): boolean { return this.preparationPendingValue; }

  acceptConsentState(state: PublicPairMediaPublicationConsentState): void {
    this.consentStateValue = state;
  }

  policyReason(sessionId: string): string | null {
    return ordinaryMediaPolicyReason(this.deps.ordinaryMediaPolicy, sessionId);
  }

  async grant(term: PublicPairMediaPublicationConsentTerm): Promise<void> {
    const session = this.authority.session;
    if (
      this.authority.kind() !== 'public'
      || !session
      || this.preparationPendingValue
    ) return;
    const preparationGeneration = ++this.preparationGeneration;
    const fence: PublicationConsentPreparationFence = Object.freeze({
      sessionId: session.id,
      securityEpoch: session.security_epoch ?? 0,
      contextGeneration: this.authority.contextGeneration,
      preparationGeneration,
    });
    this.preparationPendingValue = true;
    this.host.setOperationReason('public_media_technical_preparation_pending');
    this.host.emit();
    try {
      this.syncBinding();
      let binding = this.deps.publicationConsent.snapshot().binding;
      if (!binding) {
        this.deps.ordinaryMediaPolicy.assertActivationAllowed(session.id);
        const status = await this.deps.pairMediaE2ee.activate(session.id);
        this.assertPreparationCurrent(fence);
        if (status.sessionId !== session.id || status.state !== 'ready') {
          throw new Error(
            status.reasonCode
              || this.policyReason(session.id)
              || PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE,
          );
        }
        this.syncBinding();
        binding = this.deps.publicationConsent.snapshot().binding;
        if (
          !binding
          || binding.sessionId !== session.id
          || binding.securityEpoch !== fence.securityEpoch
          || binding.contractDigest !== status.contractDigest
        ) throw new Error('public_media_publication_consent_context_mismatch');
      }
      this.assertPreparationCurrent(fence);
      const granted = await this.deps.publicationConsent.grant(term);
      this.assertPreparationCurrent(fence);
      if (
        granted.binding?.sessionId !== fence.sessionId
        || granted.binding.securityEpoch !== fence.securityEpoch
      ) throw new Error('public_media_publication_consent_context_mismatch');
      this.consentStateValue = granted;
    } catch (error) {
      if (this.isPreparationCurrent(fence)) {
        this.host.setOperationReason(reason(
          error, 'public_media_publication_consent_grant_failed',
        ));
      }
    } finally {
      if (preparationGeneration === this.preparationGeneration) {
        this.preparationPendingValue = false;
      }
    }
    this.projectConsent();
    this.host.emit();
  }

  async revoke(): Promise<void> {
    if (this.authority.kind() !== 'public') return;
    try {
      this.consentStateValue = await this.deps.publicationConsent.revoke(
        'public_media_publication_consent_revoked',
      );
    } catch (error) {
      this.host.setOperationReason(reason(error, 'public_media_publication_consent_revoke_failed'));
    }
    // Publication consent is intentionally independent of the keyed media
    // transport. The consent service and media owners close only local
    // outbound slots; remote rendering, DataChannel and Pair login survive.
    this.projectConsent();
    this.host.emit();
  }

  /** Activates the public E2EE media contract; 'degraded' while the peer is still negotiating. */
  async activate(session: ShareSession): Promise<'ready' | 'degraded'> {
    const { ordinaryMediaPolicy, pairMediaE2ee } = this.deps;
    ordinaryMediaPolicy.assertActivationAllowed(session.id);
    const status = await pairMediaE2ee.activate(session.id);
    if (this.authority.session?.id !== session.id || this.authority.kind() !== 'public') {
      pairMediaE2ee.deactivate(session.id, 'ordinary_media_activation_stale');
      throw new Error('ordinary_media_activation_stale');
    }
    if (status.sessionId !== session.id) {
      pairMediaE2ee.deactivate(session.id, 'public_ordinary_media_e2ee_context_mismatch');
      throw new Error('public_ordinary_media_e2ee_context_mismatch');
    }
    if (status.state === 'awaiting-peer' || status.state === 'negotiating') {
      this.host.setOperationReason(this.policyReason(session.id)
        || PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE);
      return 'degraded';
    }
    if (status.state !== 'ready') {
      throw new Error(status.reasonCode || this.policyReason(session.id)
        || PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE);
    }
    this.readySessionId = session.id;
    this.failureCleanupSessionId = '';
    return 'ready';
  }

  /** Forgets readiness of the session without touching the consent record. */
  resetReadiness(): void {
    this.readySessionId = '';
    this.failureCleanupSessionId = '';
  }

  syncBinding(): void {
    const session = this.authority.session;
    if (!session || this.authority.kind() !== 'public') {
      this.deps.publicationConsent.bind(null);
      this.consentStateValue = this.deps.publicationConsent.snapshot();
      return;
    }
    let context: PublicPairMediaPublicationContext | null = null;
    try {
      context = this.deps.pairMediaE2ee.publicationContextFor(session.id);
    } catch {
      context = null;
    }
    this.deps.publicationConsent.bind(context);
    this.consentStateValue = this.deps.publicationConsent.snapshot();
  }

  cancelPreparation(): void {
    this.preparationGeneration += 1;
    this.preparationPendingValue = false;
  }

  granted(sessionId: string): boolean {
    const value = this.consentStateValue;
    return value.status === 'granted'
      && value.binding?.sessionId === sessionId
      && value.binding.securityEpoch === (this.authority.session?.security_epoch ?? 0)
      && value.expiresAtMs !== null
      && value.expiresAtMs > Date.now();
  }

  projectConsent(): void {
    const sessionId = this.authority.session?.id ?? '';
    if (!sessionId || this.authority.kind() !== 'public') return;
    const consent = this.consentStateValue;
    if (consent.status === 'granted') {
      const ready = this.deps.ordinaryMediaPolicy.allows(sessionId);
      if (ready) {
        this.readySessionId = sessionId;
        this.failureCleanupSessionId = '';
      }
      this.host.setCapability(
        'ordinary_media',
        ready ? 'authoritatively_active' : 'degraded',
        null,
        ready ? null : 'public_media_technical_preparation_pending',
      );
      return;
    }
    if (consent.status === 'granting') {
      this.host.setCapability('ordinary_media', 'sent_to_authority', null);
      return;
    }
    if (consent.status === 'revoking') {
      this.host.setCapability('ordinary_media', 'pausing', null);
      return;
    }
    const state: SemanticProgramState = consent.status === 'expired'
      ? 'expired' : consent.status === 'failed' ? 'failed' : 'revoked';
    const reasonCode = consent.reasonCode
      || (consent.status === 'expired'
        ? 'public_media_publication_consent_expired'
        : 'public_media_publication_consent_required');
    this.host.setCapability('ordinary_media', state, null, reasonCode);
  }

  reconcileStatus(status: PublicPairMediaE2eeState): void {
    const sessionId = this.authority.session?.id ?? '';
    const current = this.host.ordinaryMediaState();
    if (!sessionId || status.sessionId !== sessionId || this.authority.kind() !== 'public') {
      this.host.emit();
      return;
    }
    if (status.state === 'ready') {
      this.readySessionId = sessionId;
      this.failureCleanupSessionId = '';
      if (this.consentStateValue.status !== 'granted') {
        this.projectConsent();
        return;
      }
      if (
        !current
        || current.state === 'sent_to_authority'
        || current.state === 'degraded'
        || current.state === 'authoritatively_active'
      ) {
        this.host.setCapability('ordinary_media', 'authoritatively_active', null);
        return;
      }
    }
    if (
      status.state !== 'ready'
      && this.readySessionId === sessionId
      && current?.state !== 'revoked'
    ) {
      const failureReason = status.reasonCode || this.policyReason(sessionId)
        || PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE;
      if (this.failureCleanupSessionId !== sessionId) {
        this.failureCleanupSessionId = sessionId;
        this.deps.media.stopAudio(failureReason);
        this.deps.mediaPublications.stopAll(failureReason, true);
      }
      this.readySessionId = '';
      this.host.setCapability(
        'ordinary_media',
        status.state === 'failed' || status.state === 'inactive' ? 'failed' : 'degraded',
        null,
        failureReason,
      );
      return;
    }
    if (
      (status.state === 'awaiting-peer' || status.state === 'negotiating' || status.state === 'awaiting-security')
      && (current?.state === 'sent_to_authority' || current?.state === 'authoritatively_active')
    ) {
      this.host.setCapability(
        'ordinary_media',
        'degraded',
        current?.requestId ?? null,
        status.reasonCode || this.policyReason(sessionId),
      );
      return;
    }
    this.host.emit();
  }

  private isPreparationCurrent(fence: PublicationConsentPreparationFence): boolean {
    const session = this.authority.session;
    return fence.preparationGeneration === this.preparationGeneration
      && fence.contextGeneration === this.authority.contextGeneration
      && session?.id === fence.sessionId
      && (session.security_epoch ?? 0) === fence.securityEpoch
      && this.authority.kind() === 'public';
  }

  private assertPreparationCurrent(fence: PublicationConsentPreparationFence): void {
    if (!this.isPreparationCurrent(fence)) {
      throw new Error('public_media_publication_consent_context_changed');
    }
  }
}
