import type { SfuTransportProjectionPort } from '../../services/livekit-sfu-transport.service';
import {
  PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE,
  PairOrdinaryMediaPolicy,
} from '../../services/pair-ordinary-media.policy';
import { WebrtcMediaSessionService } from '../../services/webrtc-media-session.service';
import { MediaPublicationAuthorization } from '../../services/webrtc-media-publication.service';
import { WebrtcTransportService } from '../../services/webrtc-transport.service';
import { PublicMediaPublicationController } from './public-media-publication.controller';
import { SemanticMediaAuthorityContext } from './semantic-media-authority-context';
import { SemanticProgramState } from './semantic-media-program-shell.component';
import { reason, sessionUsable } from './semantic-media-program.models';

export interface OrdinaryMediaCaptureDeps {
  readonly ordinaryMediaPolicy: PairOrdinaryMediaPolicy;
  readonly transport: WebrtcTransportService;
  readonly media: WebrtcMediaSessionService;
  readonly sfu: SfuTransportProjectionPort;
}

export interface OrdinaryMediaCaptureHost {
  readonly ordinaryMediaState: () => SemanticProgramState | undefined;
  readonly operationReason: () => string;
}

const MAX_PUBLICATION_MS = 8 * 60 * 60 * 1_000;

/**
 * Read-only decisions about ordinary (non-semantic) audio/video capture:
 * whether activation and local capture are allowed, why not, and the bounded
 * publication authorization handed to the media publication service.
 */
export class OrdinaryMediaCapturePolicy {
  constructor(
    private readonly deps: OrdinaryMediaCaptureDeps,
    private readonly authority: SemanticMediaAuthorityContext,
    private readonly publicMedia: PublicMediaPublicationController,
    private readonly host: OrdinaryMediaCaptureHost,
  ) {}

  active(): boolean {
    const { sfu, ordinaryMediaPolicy, transport, media } = this.deps;
    const sfuState = typeof sfu.currentState === 'function'
      ? sfu.currentState()
      : (sfu.state$ as unknown as { readonly value?: { readonly status?: string } }).value;
    const sessionId = this.authority.session?.id ?? '';
    return ordinaryMediaPolicy.allows(sessionId)
      && transport.mode$.value === 'webrtc'
      && (this.authority.kind() === 'public'
        || ['active', 'muted'].includes(media.audioState$.value.status)
        || sfuState?.status === 'connected');
  }

  activationAvailable(): boolean {
    const session = this.authority.session;
    if (!sessionUsable(session)) return false;
    const authority = this.authority.kind();
    return authority === 'public'
      ? this.deps.ordinaryMediaPolicy.canActivate(session.id)
      : authority === 'hub' && this.authority.hubOnline();
  }

  activationReason(): string {
    const session = this.authority.session;
    if (!sessionUsable(session)) return 'ordinary_media_session_missing';
    try {
      this.deps.ordinaryMediaPolicy.assertActivationAllowed(session.id);
      return 'ordinary_media_activation_required';
    } catch (error) {
      return reason(error, PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE);
    }
  }

  captureAllowed(video: boolean): boolean {
    const session = this.authority.session;
    const state = this.host.ordinaryMediaState();
    const authority = this.authority.kind();
    const profileEnabled = authority === 'public'
      || this.authority.profile.semantic_media_feature_flags.ordinary_media_publication;
    return Boolean(
      session && session.revoked_at === null && this.activationAvailable()
      && this.deps.ordinaryMediaPolicy.allows(session.id)
      && this.deps.transport.mode$.value === 'webrtc'
      && profileEnabled
      && (!video || profileEnabled)
      && (authority !== 'public' || this.publicMedia.granted(session.id))
      && (state === 'authoritatively_active' || state === 'degraded'),
    );
  }

  requireCaptureAuthorization(video: boolean): void {
    if (!this.captureAllowed(video)) throw new Error(this.captureReason());
  }

  captureReason(): string {
    const session = this.authority.session;
    if (!sessionUsable(session)) return 'ordinary_media_session_missing';
    const policyReason = this.publicMedia.policyReason(session.id);
    if (policyReason) return policyReason;
    const authority = this.authority.kind();
    if (authority === 'hub' && !this.authority.profile.semantic_media_feature_flags.ordinary_media_publication) {
      return 'ordinary_media_publication_disabled';
    }
    if (this.deps.transport.mode$.value !== 'webrtc') return 'ordinary_media_webrtc_transport_required';
    if (!this.activationAvailable()) {
      return authority === 'public'
        ? this.activationReason()
        : 'ordinary_media_hub_offline';
    }
    const consent = this.publicMedia.consentState;
    if (authority === 'public' && !this.publicMedia.granted(session.id)) {
      return consent.reasonCode
        || (consent.status === 'expired'
          ? 'public_media_publication_consent_expired'
          : 'public_media_publication_consent_required');
    }
    const state = this.host.ordinaryMediaState();
    if (state !== 'authoritatively_active' && state !== 'degraded') return 'ordinary_media_activation_required';
    return this.host.operationReason();
  }

  publicationAuthorization(source: 'camera' | 'screen'): MediaPublicationAuthorization {
    this.requireCaptureAuthorization(true);
    const session = this.authority.requireSession();
    const nowMs = Date.now();
    const sessionExpiryMs = session.expires_at === null ? nowMs + MAX_PUBLICATION_MS : session.expires_at * 1_000;
    const publicationExpiryMs = this.authority.kind() === 'public'
      ? this.publicMedia.consentState.expiresAtMs ?? nowMs
      : nowMs + MAX_PUBLICATION_MS;
    const limits = source === 'camera'
      ? { width: 1280, height: 720, fps: 30, bitrate: 1_500_000 }
      : { width: 1920, height: 1080, fps: 20, bitrate: 3_000_000 };
    return Object.freeze({
      publicationId: `ordinary-${source}-${session.security_epoch ?? 1}`,
      sessionId: session.id,
      source,
      permitted: true,
      expiresAtMs: Math.min(sessionExpiryMs, publicationExpiryMs, nowMs + MAX_PUBLICATION_MS),
      maxWidth: limits.width,
      maxHeight: limits.height,
      maxFramesPerSecond: limits.fps,
      maxBitrateBps: limits.bitrate,
    });
  }
}
