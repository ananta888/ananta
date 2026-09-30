import { NetworkProfile } from '../../services/network-profile.service';
import { SemanticSfuPathContext } from '../../services/semantic-sfu-path-coordinator.service';
import { ShareParticipant, ShareSession } from '../../services/share-session.service';
import { SpeechEvidenceConsentReadModel } from '../../services/speech-evidence-consent-api.service';
import { VerifiedPeerBinding } from '../../services/webrtc-peer-key.service';
import { SemanticComputeSessionContext } from '../pair-view/semantic-compute-intent.facade';
import { PeerEvidenceSyncContext } from './peer-evidence-sync.facade';
import { SpeechEvidenceConsentContext } from './speech-evidence-consent.facade';

/**
 * Pure derivation of the per-collaborator session contexts (consent, SFU,
 * compute, evidence) from the current Pair snapshot. The facade decides when
 * to bind them; these functions only decide what to bind.
 */

export interface SemanticMediaSessionSnapshot {
  readonly session: ShareSession | null;
  readonly participants: readonly ShareParticipant[];
  readonly hubAuthority: boolean;
  readonly hubUrl: string;
  readonly localUserId: string;
  readonly binding: Readonly<VerifiedPeerBinding> | null;
  readonly profile: NetworkProfile;
}

export interface SemanticMediaSessionBindings {
  readonly consent: SpeechEvidenceConsentContext | null;
  readonly sfu: SemanticSfuPathContext | null;
  readonly computeKey: string;
  readonly compute: SemanticComputeSessionContext | null;
}

export function semanticMediaSessionBindings(snapshot: SemanticMediaSessionSnapshot): SemanticMediaSessionBindings {
  const { session, hubAuthority, hubUrl, binding } = snapshot;
  const senderId = snapshot.localUserId;
  const epoch = session?.security_epoch ?? 0;
  const hubContextAvailable = Boolean(hubAuthority && session && senderId && hubUrl && epoch > 0);
  const key = hubContextAvailable ? `${session!.id}\x1f${epoch}\x1f${senderId}` : '';
  const participants = snapshot.participants
    .filter(participant => !participant.revoked_at && participant.user_id !== senderId)
    .map(participant => participant.user_id);
  const pairContextValid = Boolean(
    hubAuthority && session && senderId && hubUrl && epoch > 0 && binding?.confirmed
    && binding.scopeId === session!.id && binding.epoch === epoch
    && binding.localPeerId === senderId && participants.includes(binding.remotePeerId),
  );
  return {
    consent: pairContextValid ? {
      hubUrl,
      tenantId: binding!.tenantId,
      sessionId: session!.id,
      epoch,
      localPeerId: senderId,
      remotePeerId: binding!.remotePeerId,
    } : null,
    sfu: hubContextAvailable ? {
      hubUrl,
      tenantId: String(session!.tenant_id || binding?.tenantId || ''),
      sessionId: session!.id,
      membershipEpoch: epoch,
      localPeerId: senderId,
      remotePeerIds: Object.freeze(participants),
      featureEnabled: snapshot.profile.semantic_media_feature_flags.semantic_media_sfu,
    } : null,
    computeKey: key,
    compute: key ? {
      hubUrl,
      sessionId: session!.id,
      epoch,
      senderId,
      consentVersion: Math.max(1, session!.permissions_version ?? 1),
    } : null,
  };
}

export function peerEvidenceSyncContext(
  snapshot: SemanticMediaSessionSnapshot,
  consent: SpeechEvidenceConsentReadModel | null,
): PeerEvidenceSyncContext | null {
  const { session, binding } = snapshot;
  const remoteActive = binding && snapshot.participants.some(participant =>
    participant.user_id === binding.remotePeerId && participant.revoked_at === null);
  if (
    !snapshot.hubAuthority
    || !session
    || !binding?.confirmed
    || !remoteActive
    || binding.scopeId !== session.id
    || binding.epoch !== (session.security_epoch ?? 0)
    || binding.localPeerId !== snapshot.localUserId
    || !consent
    || consent.consent.state !== 'active'
    || !snapshot.profile.semantic_media_feature_flags.peer_evidence_sync
    || !snapshot.hubUrl
  ) return null;
  return {
    hubUrl: snapshot.hubUrl,
    sessionId: session.id,
    pairId: session.id,
    epoch: binding.epoch,
    localPeerId: binding.localPeerId,
    remotePeerId: binding.remotePeerId,
    consent,
  };
}
