import { firstValueFrom, forkJoin, take, timeout } from 'rxjs';

import { SemanticSpeechRuntimeCoordinatorService } from '../../services/semantic-speech-runtime-coordinator.service';
import { SpeechAdapterRegistryApiService } from '../../services/speech-adapter-registry-api.service';
import { SpeechAdapterMetadata } from './reconstruction/personalized-speech-reconstructor.service';
import { SemanticMediaAuthorityContext } from './semantic-media-authority-context';
import { SemanticProgramIntent } from './semantic-media-program-shell.component';
import { SetSemanticCapability, reason } from './semantic-media-program.models';

export interface SpeechAdapterActivationHost {
  readonly emit: () => void;
  readonly setCapability: SetSemanticCapability;
}

export function assertActivatableAdapter(
  current: SpeechAdapterMetadata,
  selected: SpeechAdapterMetadata,
  pairId: string,
): void {
  const now = Date.now();
  if (
    current.adapter_id !== selected.adapter_id
    || current.pair_id !== pairId
    || current.direction !== selected.direction
    || current.registry_version !== selected.registry_version
    || current.artifact_sha256 !== selected.artifact_sha256
    || current.status !== 'approved'
    || now >= Math.min(current.expires_at_ms, current.consent_expires_at_ms)
  ) throw new Error('speech_adapter_selection_stale');
}

/**
 * Owns the Hub-approved speech adapter list of the bound session and the one
 * explicitly activated adapter, including its periodic re-validation.
 */
export class SpeechAdapterActivationController {
  private rows: readonly SpeechAdapterMetadata[] = Object.freeze([]);
  private contextKey = '';
  private generation = 0;
  private active: SpeechAdapterMetadata | null = null;
  private validationTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly api: SpeechAdapterRegistryApiService,
    private readonly runtime: SemanticSpeechRuntimeCoordinatorService,
    private readonly authority: SemanticMediaAuthorityContext,
    private readonly host: SpeechAdapterActivationHost,
  ) {}

  get adapters(): readonly SpeechAdapterMetadata[] { return this.rows; }

  get activeAdapter(): SpeechAdapterMetadata | null { return this.active; }

  refresh(): void {
    const session = this.authority.session;
    const hubUrl = this.authority.hubUrl();
    const contextKey = this.authority.hasHubAuthority()
      && session && hubUrl && this.authority.profile.semantic_media_feature_flags.speech_adapter_routing
      ? `${hubUrl}\x1f${session.id}\x1f${session.security_epoch ?? 0}`
      : '';
    if (contextKey === this.contextKey) return;
    this.contextKey = contextKey;
    const generation = ++this.generation;
    if (!contextKey || !session) {
      this.rows = Object.freeze([]);
      this.clear('speech_adapter_context_missing');
      this.host.emit();
      return;
    }
    void firstValueFrom(forkJoin([
      this.api.list(hubUrl, session.id, 'sender_to_receiver'),
      this.api.list(hubUrl, session.id, 'receiver_to_sender'),
    ]).pipe(take(1), timeout({ first: 10_000 }))).then(pages => {
      if (generation !== this.generation || contextKey !== this.contextKey) return;
      const now = Date.now();
      this.rows = Object.freeze(pages.flatMap(page => page.items).filter(row =>
        row.pair_id === session.id
        && row.status === 'approved'
        && now < Math.min(row.expires_at_ms, row.consent_expires_at_ms)
      ));
      if (this.active && !this.rows.some(row =>
        row.adapter_id === this.active?.adapter_id
        && row.direction === this.active.direction
        && row.registry_version === this.active.registry_version
      )) this.clear('speech_adapter_authority_changed');
      this.host.emit();
    }).catch(error => {
      if (generation !== this.generation) return;
      this.rows = Object.freeze([]);
      this.clear(reason(error, 'speech_adapter_registry_unavailable'));
      this.host.emit();
    });
  }

  /** Activates the explicitly selected adapter after re-reading it from the Hub registry. */
  async activate(intent: SemanticProgramIntent, sessionId: string): Promise<void> {
    const adapterId = String(intent.adapterId || '');
    const direction = intent.direction;
    if (!adapterId || !direction) throw new Error('speech_adapter_explicit_selection_required');
    const selected = this.rows.find(
      row => row.adapter_id === adapterId && row.direction === direction,
    );
    if (!selected) throw new Error('speech_adapter_selection_stale');
    const current = await firstValueFrom(
      this.api.get(this.authority.hubUrl(), adapterId, sessionId, direction).pipe(
        take(1),
        timeout({ first: 10_000 }),
      ),
    );
    assertActivatableAdapter(current, selected, sessionId);
    await this.runtime.activatePersonalization({
      metadata: current,
      context: {
        pairId: current.pair_id,
        direction: current.direction,
        speakerDigest: current.speaker_digest,
        scopeDigest: current.scope_digest,
        baseModelId: current.base_model_id,
        baseModelDigest: current.base_model_digest,
        consentDigest: current.consent_digest,
      },
    });
    this.active = current;
    this.armValidation();
  }

  clear(reasonCode: string, notifyRuntime = true): void {
    const current = this.active;
    this.active = null;
    if (this.validationTimer !== null) {
      globalThis.clearTimeout(this.validationTimer);
      this.validationTimer = null;
    }
    if (current && notifyRuntime) void this.runtime.revokePersonalization(current.adapter_id);
    if (reasonCode === 'speech_adapter_browser_engine_not_released') {
      this.host.setCapability('adapter_activation', 'failed', null, reasonCode);
    }
  }

  /** Forgets the session-scoped adapter list; pending registry reads become stale. */
  resetSessionScope(): void {
    this.rows = Object.freeze([]);
    this.contextKey = '';
    this.generation += 1;
  }

  private armValidation(): void {
    if (this.validationTimer !== null) globalThis.clearTimeout(this.validationTimer);
    const current = this.active;
    if (!current) return;
    const delay = Math.max(1, Math.min(15_000, current.expires_at_ms - Date.now(), current.consent_expires_at_ms - Date.now()));
    this.validationTimer = globalThis.setTimeout(() => {
      this.validationTimer = null;
      void this.validateActive();
    }, delay);
  }

  private async validateActive(): Promise<void> {
    const expected = this.active;
    if (!expected) return;
    if (!this.authority.hasHubAuthority()) {
      this.clear('speech_adapter_hub_authority_lost');
      this.host.setCapability(
        'adapter_activation', 'revoked', null, 'speech_adapter_hub_authority_lost',
      );
      return;
    }
    try {
      const current = await firstValueFrom(
        this.api.get(this.authority.hubUrl(), expected.adapter_id, expected.pair_id, expected.direction).pipe(
          take(1), timeout({ first: 10_000 }),
        ),
      );
      assertActivatableAdapter(current, expected, expected.pair_id);
      if (this.active !== expected) return;
      this.active = current;
      await this.runtime.cleanupPersonalization();
      this.armValidation();
    } catch (error) {
      if (this.active !== expected) return;
      this.clear(reason(error, 'speech_adapter_authority_changed'));
      this.host.setCapability(
        'adapter_activation', 'revoked', null,
        reason(error, 'speech_adapter_authority_changed'),
      );
    }
    this.host.emit();
  }
}
