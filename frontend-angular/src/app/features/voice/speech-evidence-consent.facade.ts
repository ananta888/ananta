import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, firstValueFrom } from 'rxjs';

import {
  SPEECH_EVIDENCE_CONSENT_SCHEMA,
  SPEECH_EVIDENCE_GRANTS,
  SpeechEvidenceConsentApiService,
  SpeechEvidenceConsentDocument,
  SpeechEvidenceConsentReadModel,
  SpeechEvidenceDataClass,
  SpeechEvidenceGrant,
} from '../../services/speech-evidence-consent-api.service';

export interface SpeechEvidenceConsentContext {
  readonly hubUrl: string;
  readonly tenantId: string;
  readonly sessionId: string;
  readonly epoch: number;
  readonly localPeerId: string;
  readonly remotePeerId: string;
}

export interface SpeechEvidenceConsentDraft {
  readonly consentId: string;
  readonly purpose: string;
  readonly dataClasses: readonly SpeechEvidenceDataClass[];
  readonly retentionSeconds: number;
  readonly trainerLocations: readonly string[];
  readonly grants: Readonly<Record<SpeechEvidenceGrant, boolean>>;
  readonly expiresInHours: number;
  readonly signerDigests: Readonly<Record<string, string>>;
}

export type SpeechEvidenceConsentIntent =
  | Readonly<{ kind: 'load'; consentId: string }>
  | Readonly<{ kind: 'grant' | 'reduce' | 'renew'; draft: SpeechEvidenceConsentDraft }>
  | Readonly<{ kind: 'revoke' }>;

export interface SpeechEvidenceConsentPanelState {
  readonly bound: boolean;
  readonly signerIds: readonly string[];
  readonly consent: SpeechEvidenceConsentReadModel | null;
  readonly pending: boolean;
  readonly errorCode: string | null;
}

const EMPTY: SpeechEvidenceConsentPanelState = Object.freeze({
  bound: false,
  signerIds: Object.freeze([]),
  consent: null,
  pending: false,
  errorCode: 'speech_consent_context_missing',
});

@Injectable()
export class SpeechEvidenceConsentFacade implements OnDestroy {
  private readonly api = inject(SpeechEvidenceConsentApiService);
  private context: SpeechEvidenceConsentContext | null = null;
  private serial = 0;
  readonly state$ = new BehaviorSubject<SpeechEvidenceConsentPanelState>(EMPTY);

  bind(context: SpeechEvidenceConsentContext | null): void {
    const next = context ? Object.freeze({ ...context }) : null;
    if (contextKey(next) === contextKey(this.context)) return;
    this.context = next;
    this.state$.next(next ? Object.freeze({
      bound: true,
      signerIds: Object.freeze([next.localPeerId, next.remotePeerId].sort()),
      consent: null,
      pending: false,
      errorCode: null,
    }) : EMPTY);
  }

  async handle(intent: SpeechEvidenceConsentIntent): Promise<void> {
    const context = this.context;
    if (!context || this.state$.value.pending) return;
    this.patch({ pending: true, errorCode: null });
    try {
      let result: SpeechEvidenceConsentReadModel;
      if (intent.kind === 'load') {
        result = await firstValueFrom(this.api.get(context.hubUrl, intent.consentId));
      } else if (intent.kind === 'revoke') {
        const current = this.requireCurrent();
        result = await firstValueFrom(this.api.revoke(
          context.hubUrl,
          current.consent.consent_id,
          current.consent.consent_version,
          context.localPeerId,
          this.key('revoke'),
        ));
      } else {
        const document = this.document(context, intent.draft, intent.kind);
        if (intent.kind === 'grant') {
          result = await firstValueFrom(this.api.grant(context.hubUrl, document, this.key('grant')));
        } else {
          const current = this.requireCurrent();
          result = await firstValueFrom(this.api[intent.kind](
            context.hubUrl,
            document,
            current.consent.consent_version,
            this.key(intent.kind),
          ));
        }
      }
      if (context !== this.context) return;
      if (!matchesContext(result.consent, context)) throw new Error('speech_consent_context_mismatch');
      this.patch({ consent: result, pending: false, errorCode: null });
    } catch (error) {
      if (context === this.context) this.patch({ pending: false, errorCode: reason(error) });
    }
  }

  ngOnDestroy(): void { this.state$.complete(); }

  private document(
    context: SpeechEvidenceConsentContext,
    draft: SpeechEvidenceConsentDraft,
    operation: 'grant' | 'reduce' | 'renew',
  ): SpeechEvidenceConsentDocument {
    const current = this.state$.value.consent?.consent ?? null;
    if (operation !== 'grant' && !current) throw new Error('speech_consent_not_loaded');
    const now = Date.now();
    const signerIds = [context.localPeerId, context.remotePeerId].sort();
    const signatures = operation === 'grant' ? { ...draft.signerDigests } : { ...current!.signatures };
    if (signerIds.some(id => !/^[0-9a-f]{64}$/.test(signatures[id] ?? ''))) {
      throw new Error('speech_consent_bilateral_signature_digest_required');
    }
    const grants = Object.fromEntries(
      SPEECH_EVIDENCE_GRANTS.map(name => [name, draft.grants[name] === true]),
    ) as Record<SpeechEvidenceGrant, boolean>;
    const expiresAt = operation === 'reduce'
      ? Math.min(current!.expires_at_ms, now + draft.expiresInHours * 3_600_000)
      : now + draft.expiresInHours * 3_600_000;
    return Object.freeze({
      schema: SPEECH_EVIDENCE_CONSENT_SCHEMA,
      consent_id: operation === 'grant' ? draft.consentId : current!.consent_id,
      tenant_id: context.tenantId,
      owner_subject: context.localPeerId,
      speaker_id: context.localPeerId,
      recipient_id: context.remotePeerId,
      direction: 'sender_to_receiver',
      pair_id: context.sessionId,
      session_id: context.sessionId,
      session_epoch: context.epoch,
      purpose: draft.purpose,
      data_classes: Object.freeze([...new Set(draft.dataClasses)].sort()),
      retention_seconds: draft.retentionSeconds,
      trainer_locations: Object.freeze([...new Set(draft.trainerLocations)].sort()),
      grants: Object.freeze(grants),
      consent_version: current?.consent_version ?? 1,
      revocation_epoch: current?.revocation_epoch ?? 0,
      issued_at_ms: operation === 'reduce' ? current!.issued_at_ms : now,
      expires_at_ms: expiresAt,
      state: 'active',
      required_signers: Object.freeze(signerIds),
      signatures: Object.freeze(Object.fromEntries(signerIds.map(id => [id, signatures[id]]))),
    });
  }

  private requireCurrent(): SpeechEvidenceConsentReadModel {
    const current = this.state$.value.consent;
    if (!current) throw new Error('speech_consent_not_loaded');
    return current;
  }

  private patch(patch: Partial<SpeechEvidenceConsentPanelState>): void {
    this.state$.next(Object.freeze({ ...this.state$.value, ...patch }));
  }

  private key(action: string): string {
    return `speech-consent-${action}-${++this.serial}-${crypto.randomUUID()}`;
  }
}

function contextKey(value: SpeechEvidenceConsentContext | null): string {
  return value
    ? [value.hubUrl, value.tenantId, value.sessionId, value.epoch, value.localPeerId, value.remotePeerId].join('\0')
    : '';
}

function matchesContext(value: SpeechEvidenceConsentDocument, context: SpeechEvidenceConsentContext): boolean {
  const participants = new Set([value.speaker_id, value.recipient_id]);
  return value.tenant_id === context.tenantId
    && value.owner_subject === value.speaker_id
    && participants.size === 2
    && participants.has(context.localPeerId)
    && participants.has(context.remotePeerId)
    && value.pair_id === context.sessionId
    && value.session_id === context.sessionId
    && value.session_epoch === context.epoch
    && value.direction === 'sender_to_receiver'
    && value.required_signers.length === 2
    && value.required_signers.every(signer => participants.has(signer));
}

function reason(error: unknown): string {
  if (error && typeof error === 'object') {
    const value = error as { error?: { error?: { code?: unknown } | string }; message?: unknown };
    const nested = value.error?.error;
    if (typeof nested === 'string' && /^[a-z][a-z0-9_]{2,159}$/.test(nested)) return nested;
    if (nested && typeof nested === 'object' && typeof nested.code === 'string') return nested.code;
    if (typeof value.message === 'string' && /^[a-z][a-z0-9_]{2,159}$/.test(value.message)) return value.message;
  }
  return 'speech_consent_request_failed';
}
