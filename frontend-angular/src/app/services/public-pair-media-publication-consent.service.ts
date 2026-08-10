import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable } from 'rxjs';

import { PAIR_MEDIA_OUTBOUND_PUBLICATION_GATE } from './pair-media-outbound-publication-gate.port';
import {
  PUBLIC_PAIR_MEDIA_GRANTS,
  type PublicPairMediaSlot,
} from './public-pair-media-security-contract';

export type PublicPairMediaPublicationConsentStatus =
  | 'unbound'
  | 'inactive'
  | 'granting'
  | 'granted'
  | 'revoking'
  | 'revoked'
  | 'expired'
  | 'failed';

export type PublicPairMediaPublicationConsentTerm =
  | Readonly<{ kind: 'session' }>
  | Readonly<{ kind: 'timed'; durationMs: 900_000 | 3_600_000 }>;

export interface PublicPairMediaPublicationConsentBinding {
  readonly sessionId: string;
  readonly securityEpoch: number;
  readonly contractDigest: string;
  readonly adapterGeneration: number;
  readonly localPeerId: string;
  readonly remotePeerId: string;
  readonly maxExpiresAtMs: number;
}

export interface PublicPairMediaPublicationConsentState {
  readonly status: PublicPairMediaPublicationConsentStatus;
  readonly binding: Readonly<PublicPairMediaPublicationConsentBinding> | null;
  readonly revision: number;
  readonly term: Readonly<PublicPairMediaPublicationConsentTerm> | null;
  readonly slots: readonly PublicPairMediaSlot[];
  readonly expiresAtMs: number | null;
  readonly reasonCode?: string;
}

const CONSENT_SLOTS = PUBLIC_PAIR_MEDIA_GRANTS as readonly PublicPairMediaSlot[];
const DIGEST_RE = /^[a-f0-9]{64}$/;
const PEER_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/;
const REASON_RE = /^[a-z][a-z0-9_]{2,119}$/;
const MAX_TIMER_DELAY_MS = 2_147_483_647;

/**
 * Owns this browser's runtime-only permission to publish Public Pair media.
 *
 * The authority-signed media contract and bilateral E2EE handshake remain the
 * technical receive/transport authority. This narrower state only controls
 * whether the local browser may attach microphone, camera or screen tracks.
 */
@Injectable({ providedIn: 'root' })
export class PublicPairMediaPublicationConsentService {
  private readonly transforms = inject(PAIR_MEDIA_OUTBOUND_PUBLICATION_GATE);
  private readonly stateSubject = new BehaviorSubject<PublicPairMediaPublicationConsentState>(
    state('unbound', null, 0, null, null, 'public_media_publication_consent_unbound'),
  );
  readonly state$: Observable<PublicPairMediaPublicationConsentState> =
    this.stateSubject.asObservable();

  private revisionSerial = 0;
  private expiryTimer: ReturnType<typeof setTimeout> | null = null;

  get currentState(): PublicPairMediaPublicationConsentState {
    this.expireIfRequired();
    return this.stateSubject.value;
  }

  snapshot(): PublicPairMediaPublicationConsentState {
    return this.currentState;
  }

  bind(binding: Readonly<PublicPairMediaPublicationConsentBinding> | null): void {
    const normalized = binding ? normalizeBinding(binding) : null;
    const current = this.stateSubject.value;
    if (sameBinding(current.binding, normalized)) return;

    this.clearExpiryTimer();
    const revision = this.nextRevision();
    const previous = current.binding;
    this.stateSubject.next(normalized
      ? state(
        'inactive', normalized, revision, null, null,
        'public_media_publication_consent_required',
      )
      : state(
        'unbound', null, revision, null, null,
        'public_media_publication_consent_unbound',
      ));

    if (previous) void this.disableGate(previous, revision);
    if (normalized) void this.disableGate(normalized, revision);
  }

  async grant(
    term: Readonly<PublicPairMediaPublicationConsentTerm>,
  ): Promise<PublicPairMediaPublicationConsentState> {
    const binding = this.requireBinding();
    if (this.stateSubject.value.status === 'granting'
        || this.stateSubject.value.status === 'revoking') {
      throw new Error('public_media_publication_consent_operation_pending');
    }
    if (this.stateSubject.value.status === 'failed') {
      throw new Error(
        this.stateSubject.value.reasonCode ?? 'public_media_publication_consent_failed',
      );
    }
    const normalizedTerm = normalizeTerm(term);
    const nowMs = Date.now();
    const expiresAtMs = normalizedTerm.kind === 'session'
      ? binding.maxExpiresAtMs
      : Math.min(binding.maxExpiresAtMs, nowMs + normalizedTerm.durationMs);
    if (expiresAtMs <= nowMs) {
      this.markExpired(binding, 'public_media_publication_consent_expired');
      return this.stateSubject.value;
    }

    this.clearExpiryTimer();
    const revision = this.nextRevision();
    this.stateSubject.next(state(
      'granting', binding, revision, normalizedTerm, expiresAtMs,
      'public_media_publication_consent_granting',
    ));
    try {
      await this.transforms.setOutboundPublicationGate(
        binding.sessionId,
        binding.adapterGeneration,
        { revision, enabled: true, slots: CONSENT_SLOTS, expiresAtMs },
      );
      if (!this.isCurrent(binding, revision, 'granting')) return this.stateSubject.value;
      this.stateSubject.next(state(
        'granted', binding, revision, normalizedTerm, expiresAtMs,
      ));
      this.armExpiry(binding, revision, expiresAtMs);
      return this.stateSubject.value;
    } catch (error) {
      if (this.isCurrent(binding, revision, 'granting')) {
        await this.failClosed(
          binding,
          reason(error, 'public_media_publication_consent_gate_failed'),
        );
      }
      return this.stateSubject.value;
    }
  }

  async revoke(
    reasonCode = 'public_media_publication_consent_revoked',
  ): Promise<PublicPairMediaPublicationConsentState> {
    const binding = this.requireBinding();
    this.clearExpiryTimer();
    // Publish the non-granted state before the first await. Media services use
    // this edge to invalidate pending permissions and stop owned tracks.
    const revision = this.nextRevision();
    const normalizedReason = reasonValue(reasonCode, 'public_media_publication_consent_revoked');
    this.stateSubject.next(state(
      'revoking', binding, revision, null, null, normalizedReason,
    ));
    try {
      await this.transforms.setOutboundPublicationGate(
        binding.sessionId,
        binding.adapterGeneration,
        { revision, enabled: false, slots: CONSENT_SLOTS, expiresAtMs: 0 },
      );
      if (!this.isCurrent(binding, revision, 'revoking')) return this.stateSubject.value;
      this.stateSubject.next(state(
        'revoked', binding, revision, null, null, normalizedReason,
      ));
    } catch (error) {
      if (this.isCurrent(binding, revision, 'revoking')) {
        this.stateSubject.next(state(
          'failed', binding, revision, null, null,
          reason(error, 'public_media_publication_consent_gate_failed'),
        ));
      }
    }
    return this.stateSubject.value;
  }

  allows(sessionId: string, slot: PublicPairMediaSlot): boolean {
    this.expireIfRequired();
    const current = this.stateSubject.value;
    return current.status === 'granted'
      && current.binding?.sessionId === sessionId
      && current.expiresAtMs !== null
      && current.expiresAtMs > Date.now()
      && current.slots.includes(slot);
  }

  assertAllowed(sessionId: string, slot: PublicPairMediaSlot): void {
    if (this.allows(sessionId, slot)) return;
    const current = this.stateSubject.value;
    if (!current.binding || current.binding.sessionId !== sessionId) {
      throw new Error('public_media_publication_consent_context_mismatch');
    }
    if (!CONSENT_SLOTS.includes(slot)) {
      throw new Error('public_media_publication_consent_slot_denied');
    }
    throw new Error(current.reasonCode ?? consentReason(current.status));
  }

  private requireBinding(): Readonly<PublicPairMediaPublicationConsentBinding> {
    const binding = this.stateSubject.value.binding;
    if (!binding) throw new Error('public_media_publication_consent_unbound');
    if (binding.maxExpiresAtMs <= Date.now()) {
      this.markExpired(binding, 'public_media_publication_consent_expired');
      throw new Error('public_media_publication_consent_expired');
    }
    return binding;
  }

  private armExpiry(
    binding: Readonly<PublicPairMediaPublicationConsentBinding>,
    revision: number,
    expiresAtMs: number,
  ): void {
    this.clearExpiryTimer();
    const delay = Math.max(1, Math.min(MAX_TIMER_DELAY_MS, expiresAtMs - Date.now()));
    this.expiryTimer = setTimeout(() => {
      this.expiryTimer = null;
      if (!this.isCurrent(binding, revision, 'granted')) return;
      if (expiresAtMs > Date.now()) {
        this.armExpiry(binding, revision, expiresAtMs);
        return;
      }
      this.markExpired(binding, 'public_media_publication_consent_expired');
    }, delay);
  }

  private expireIfRequired(): void {
    const current = this.stateSubject.value;
    if (
      current.binding
      && current.status === 'granted'
      && current.expiresAtMs !== null
      && current.expiresAtMs <= Date.now()
    ) this.markExpired(current.binding, 'public_media_publication_consent_expired');
  }

  private markExpired(
    binding: Readonly<PublicPairMediaPublicationConsentBinding>,
    reasonCode: string,
  ): void {
    this.clearExpiryTimer();
    const revision = this.nextRevision();
    this.stateSubject.next(state(
      'expired', binding, revision, null, null, reasonCode,
    ));
    void this.disableGate(binding, revision, reasonCode);
  }

  private async failClosed(
    binding: Readonly<PublicPairMediaPublicationConsentBinding>,
    reasonCode: string,
  ): Promise<void> {
    this.clearExpiryTimer();
    const revision = this.nextRevision();
    this.stateSubject.next(state(
      'failed', binding, revision, null, null, reasonCode,
    ));
    await this.disableGate(binding, revision, reasonCode);
  }

  private async disableGate(
    binding: Readonly<PublicPairMediaPublicationConsentBinding>,
    revision: number,
    _reasonCode = 'public_media_publication_consent_not_granted',
  ): Promise<void> {
    try {
      await this.transforms.setOutboundPublicationGate(
        binding.sessionId,
        binding.adapterGeneration,
        { revision, enabled: false, slots: CONSENT_SLOTS, expiresAtMs: 0 },
      );
    } catch {
      // The adapter's ACK boundary is fail-closed. Local state was made
      // non-granted before this await and capture subscribers already stopped.
    }
  }

  private isCurrent(
    binding: Readonly<PublicPairMediaPublicationConsentBinding>,
    revision: number,
    status: PublicPairMediaPublicationConsentStatus,
  ): boolean {
    const current = this.stateSubject.value;
    return current.status === status
      && current.revision === revision
      && sameBinding(current.binding, binding);
  }

  private nextRevision(): number {
    this.revisionSerial += 1;
    if (!Number.isSafeInteger(this.revisionSerial)) {
      throw new Error('public_media_publication_consent_revision_exhausted');
    }
    return this.revisionSerial;
  }

  private clearExpiryTimer(): void {
    if (this.expiryTimer !== null) clearTimeout(this.expiryTimer);
    this.expiryTimer = null;
  }
}

function state(
  status: PublicPairMediaPublicationConsentStatus,
  binding: Readonly<PublicPairMediaPublicationConsentBinding> | null,
  revision: number,
  term: Readonly<PublicPairMediaPublicationConsentTerm> | null,
  expiresAtMs: number | null,
  reasonCode?: string,
): PublicPairMediaPublicationConsentState {
  return Object.freeze({
    status,
    binding,
    revision,
    term,
    slots: CONSENT_SLOTS,
    expiresAtMs,
    ...(reasonCode ? { reasonCode } : {}),
  });
}

function normalizeBinding(
  value: Readonly<PublicPairMediaPublicationConsentBinding>,
): Readonly<PublicPairMediaPublicationConsentBinding> {
  if (
    !value
    || !PEER_ID_RE.test(value.sessionId)
    || !Number.isSafeInteger(value.securityEpoch)
    || value.securityEpoch < 1
    || !DIGEST_RE.test(value.contractDigest)
    || !Number.isSafeInteger(value.adapterGeneration)
    || value.adapterGeneration < 1
    || !PEER_ID_RE.test(value.localPeerId)
    || !PEER_ID_RE.test(value.remotePeerId)
    || value.localPeerId === value.remotePeerId
    || !Number.isSafeInteger(value.maxExpiresAtMs)
    || value.maxExpiresAtMs < 1
  ) throw new Error('public_media_publication_consent_binding_invalid');
  return Object.freeze({ ...value });
}

function normalizeTerm(
  value: Readonly<PublicPairMediaPublicationConsentTerm>,
): Readonly<PublicPairMediaPublicationConsentTerm> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('public_media_publication_consent_term_invalid');
  }
  const fields = Object.keys(value).sort();
  if (value.kind === 'session' && fields.length === 1 && fields[0] === 'kind') {
    return Object.freeze({ kind: 'session' });
  }
  if (
    value.kind === 'timed'
    && fields.length === 2
    && fields[0] === 'durationMs'
    && fields[1] === 'kind'
    && (value.durationMs === 900_000 || value.durationMs === 3_600_000)
  ) return Object.freeze({ kind: 'timed', durationMs: value.durationMs });
  throw new Error('public_media_publication_consent_term_invalid');
}

function sameBinding(
  left: Readonly<PublicPairMediaPublicationConsentBinding> | null,
  right: Readonly<PublicPairMediaPublicationConsentBinding> | null,
): boolean {
  return left === right || Boolean(
    left && right
    && left.sessionId === right.sessionId
    && left.securityEpoch === right.securityEpoch
    && left.contractDigest === right.contractDigest
    && left.adapterGeneration === right.adapterGeneration
    && left.localPeerId === right.localPeerId
    && left.remotePeerId === right.remotePeerId
    && left.maxExpiresAtMs === right.maxExpiresAtMs,
  );
}

function reason(error: unknown, fallback: string): string {
  return error instanceof Error ? reasonValue(error.message, fallback) : fallback;
}

function reasonValue(value: string, fallback: string): string {
  return REASON_RE.test(value) ? value : fallback;
}

function consentReason(status: PublicPairMediaPublicationConsentStatus): string {
  return ({
    unbound: 'public_media_publication_consent_unbound',
    inactive: 'public_media_publication_consent_required',
    granting: 'public_media_publication_consent_granting',
    revoking: 'public_media_publication_consent_revoking',
    revoked: 'public_media_publication_consent_revoked',
    expired: 'public_media_publication_consent_expired',
    failed: 'public_media_publication_consent_failed',
  } as Partial<Record<PublicPairMediaPublicationConsentStatus, string>>)[status]
    ?? 'public_media_publication_consent_required';
}
