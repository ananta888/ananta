import { ShareSession } from '../../services/share-session.service';
import { SpeechEvidenceConsentReadModel } from '../../services/speech-evidence-consent-api.service';
import { VerifiedPeerBinding } from '../../services/webrtc-peer-key.service';

/**
 * Pure consent checks of the semantic media program: which consent read model
 * authorizes offline speech reconciliation, evidence sharing and live speech
 * corrections for the currently bound Pair. No state, no side effects.
 */

export function activeReconciliationConsent(
  readModel: SpeechEvidenceConsentReadModel | null,
  session: ShareSession | null,
  binding: Readonly<VerifiedPeerBinding> | null,
  localPeerId: string,
): SpeechEvidenceConsentReadModel | null {
  const consent = readModel?.consent;
  if (
    !session || !readModel || !consent || !binding?.confirmed || !localPeerId
    || consent.state !== 'active' || consent.expires_at_ms <= Date.now()
    || consent.tenant_id !== binding.tenantId
    || consent.owner_subject !== localPeerId
    || consent.session_id !== session.id || consent.pair_id !== session.id
    || consent.session_epoch !== (session.security_epoch ?? 0)
    || consent.purpose !== 'speech_reconciliation'
    || !consent.data_classes.includes('audio')
    || consent.grants.raw_audio_share !== true
    || consent.grants.dataset_import !== true
    || !consent.required_signers.includes(binding.localPeerId)
    || !consent.required_signers.includes(binding.remotePeerId)
  ) return null;
  return readModel;
}

export function reconciliationConsentClaimKey(value: SpeechEvidenceConsentReadModel | null): string {
  const consent = value?.consent;
  if (!value || !consent) return '';
  return [
    value.consentDigest,
    value.scopeDigest,
    consent.consent_id,
    consent.consent_version,
    consent.revocation_epoch,
    consent.state,
    consent.expires_at_ms,
    consent.purpose,
    [...consent.data_classes].sort().join(','),
    consent.grants.raw_audio_share ? 1 : 0,
    consent.grants.dataset_import ? 1 : 0,
    consent.grants.training ? 1 : 0,
    [...consent.trainer_locations].sort().join(','),
  ].join('\x1f');
}

export function requireActiveEvidenceConsent(
  readModel: SpeechEvidenceConsentReadModel | null,
  session: ShareSession,
) {
  const value = readModel?.consent;
  if (!value) throw new Error('speech_evidence_consent_required');
  if (value.state !== 'active' || value.expires_at_ms <= Date.now()) throw new Error('speech_evidence_consent_inactive');
  if (value.session_id !== session.id || value.session_epoch !== (session.security_epoch ?? 0)) {
    throw new Error('speech_evidence_consent_context_mismatch');
  }
  if (!value.grants.transcript_share && !value.grants.feature_share) {
    throw new Error('speech_evidence_share_grant_required');
  }
  return value;
}

/** Runtime context for the live speech coordinator, including the optional correction consent. */
export function speechRuntimeContext(
  hubUrl: string,
  binding: Readonly<VerifiedPeerBinding>,
  session: ShareSession,
  readModel: SpeechEvidenceConsentReadModel | null,
) {
  const consent = readModel?.consent;
  const grants = consent?.grants;
  const classes = new Set(consent?.data_classes ?? []);
  const correctionConsent = consent
    && consent.state === 'active'
    && consent.session_id === session.id
    && consent.session_epoch === binding.epoch
    && consent.expires_at_ms > Date.now()
    && grants?.capture === true
    && grants?.raw_audio_share === true
    && grants?.transcript_share === true
    && classes.has('audio')
    && classes.has('transcript')
    && classes.has('correction')
    && /^[a-f0-9]{64}$/.test(String(readModel?.consentDigest || ''))
    ? Object.freeze({
        consentId: consent.consent_id,
        consentDigest: readModel!.consentDigest,
        consentVersion: consent.consent_version,
        revocationEpoch: consent.revocation_epoch,
        expiresAtMs: consent.expires_at_ms,
      })
    : undefined;
  return Object.freeze({
    hubUrl,
    sessionId: binding.scopeId,
    epoch: binding.epoch,
    localPeerId: binding.localPeerId,
    remotePeerId: binding.remotePeerId,
    consentVersion: Math.max(1, session.permissions_version ?? 1),
    contractDigest: binding.contractDigest,
    ...(correctionConsent ? { correctionConsent } : {}),
  });
}
