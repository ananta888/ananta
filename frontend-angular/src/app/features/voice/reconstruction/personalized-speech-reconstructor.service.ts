import { Injectable, InjectionToken, inject } from '@angular/core';

import { ReceiverSpeechAdapterCacheService } from './receiver-speech-adapter-cache.service';

export type SpeechDirection = 'sender_to_receiver' | 'receiver_to_sender';
export type SpeechFallbackMode = 'adapted' | 'base' | 'ordinary_audio' | 'unavailable';

export interface SpeechAdapterMetadata {
  adapter_id: string;
  pair_id: string;
  direction: SpeechDirection;
  speaker_digest: string;
  scope_digest: string;
  base_model_id: string;
  base_model_digest: string;
  consent_digest: string;
  artifact_ref: string;
  artifact_sha256: string;
  expires_at_ms: number;
  consent_expires_at_ms: number;
  registry_version: number;
  status: 'evaluated' | 'approved' | 'revoked' | 'deprecated' | 'expired';
}

export interface ReceiverSpeechContext {
  pairId: string;
  direction: SpeechDirection;
  speakerDigest: string;
  scopeDigest: string;
  baseModelId: string;
  baseModelDigest: string;
  consentDigest: string;
}

export interface ReceiverLoadedSpeechAdapter {
  adapterId: string;
  artifactSha256: string;
  handle: unknown;
}

export interface ReceiverSpeechAdapterEngine {
  readonly implementationKind: 'production' | 'test_mock';
  loadLocal(artifactRef: string, expectedSha256: string, baseModelId: string): Promise<ReceiverLoadedSpeechAdapter>;
  infer(loaded: ReceiverLoadedSpeechAdapter, semanticPayload: Uint8Array): Promise<Uint8Array>;
  reconstructBase(semanticPayload: Uint8Array): Promise<Uint8Array>;
  unload(loaded: ReceiverLoadedSpeechAdapter): Promise<void>;
  clearLocalArtifact(artifactSha256: string): Promise<void>;
}

export interface PersonalizedSpeechResult {
  mode: SpeechFallbackMode;
  audio: Uint8Array;
  reasonCode: string | null;
  adapterId: string | null;
}

export const RECEIVER_SPEECH_ADAPTER_ENGINE = new InjectionToken<ReceiverSpeechAdapterEngine>(
  'RECEIVER_SPEECH_ADAPTER_ENGINE',
  {
    providedIn: 'root',
    factory: () => inject(FailClosedReceiverSpeechAdapterEngine),
  },
);

/**
 * Production placeholder until a reviewed browser model runtime is shipped.
 * It cannot silently claim personalization and only owns cache destruction.
 */
@Injectable({ providedIn: 'root' })
export class FailClosedReceiverSpeechAdapterEngine implements ReceiverSpeechAdapterEngine {
  readonly implementationKind = 'production' as const;
  private readonly cache = inject(ReceiverSpeechAdapterCacheService);

  async loadLocal(): Promise<ReceiverLoadedSpeechAdapter> {
    throw new Error('speech_adapter_browser_engine_not_released');
  }

  async infer(): Promise<Uint8Array> {
    throw new Error('speech_adapter_browser_engine_not_released');
  }

  async reconstructBase(): Promise<Uint8Array> {
    throw new Error('speech_adapter_browser_engine_not_released');
  }

  async unload(): Promise<void> { /* no production handle can be created */ }

  async clearLocalArtifact(artifactSha256: string): Promise<void> {
    this.cache.remove(artifactSha256);
  }
}

@Injectable({ providedIn: 'root' })
export class PersonalizedSpeechReconstructorService {
  private readonly engine = inject(RECEIVER_SPEECH_ADAPTER_ENGINE);
  private loaded: { metadata: SpeechAdapterMetadata; adapter: ReceiverLoadedSpeechAdapter } | null = null;
  private activationGeneration = 0;
  private pendingAdapterId: string | null = null;

  async activate(metadata: SpeechAdapterMetadata, context: ReceiverSpeechContext, nowMs?: number): Promise<void> {
    const authority = Object.freeze({ ...metadata });
    const authorityContext = Object.freeze({ ...context });
    const generation = ++this.activationGeneration;
    this.pendingAdapterId = authority.adapter_id;
    try {
      await this.assertCurrent(authority, authorityContext, nowMs ?? Date.now());
    } catch (error) {
      if (generation === this.activationGeneration) this.pendingAdapterId = null;
      throw error;
    }
    if (
      this.loaded?.metadata.adapter_id === authority.adapter_id
      && this.loaded.metadata.registry_version === authority.registry_version
      && this.loaded.adapter.artifactSha256 === authority.artifact_sha256
      && sameAuthority(this.loaded.metadata, authority)
    ) {
      if (generation === this.activationGeneration) this.pendingAdapterId = null;
      return;
    }
    let loaded: ReceiverLoadedSpeechAdapter | null = null;
    try {
      await this.releaseLoaded();
      loaded = await this.engine.loadLocal(
        authority.artifact_ref,
        authority.artifact_sha256,
        authority.base_model_id,
      );
      if (loaded.adapterId !== authority.adapter_id || loaded.artifactSha256 !== authority.artifact_sha256) {
        throw new Error('speech_adapter_load_binding_mismatch');
      }

      // The Hub/facade may revoke or replace the selected registry version
      // while local weights are loading. Re-evaluate every available
      // authority field after that interval and require the activation
      // generation to still be current before making the handle reachable.
      await this.assertCurrent(metadata, context, nowMs ?? Date.now());
      if (
        generation !== this.activationGeneration
        || this.pendingAdapterId !== authority.adapter_id
        || !sameAuthority(authority, metadata)
        || !sameContext(authorityContext, context)
      ) throw new Error('speech_adapter_authority_changed');

      this.loaded = { metadata: { ...authority }, adapter: loaded };
      loaded = null;
    } catch (error) {
      if (loaded) await this.safeUnload(loaded);
      await this.engine.clearLocalArtifact(authority.artifact_sha256);
      throw error;
    } finally {
      if (generation === this.activationGeneration) this.pendingAdapterId = null;
    }
  }

  async reconstruct(
    metadata: SpeechAdapterMetadata,
    context: ReceiverSpeechContext,
    semanticPayload: Uint8Array,
    ordinaryAudio?: Uint8Array,
    nowMs?: number,
  ): Promise<PersonalizedSpeechResult> {
    try {
      if (!(semanticPayload instanceof Uint8Array) || semanticPayload.byteLength > 8 * 1024 * 1024) {
        throw new Error('speech_payload_invalid');
      }
      await this.assertCurrent(metadata, context, nowMs ?? Date.now());
      await this.activate(metadata, context, nowMs);
      if (!this.loaded) throw new Error('speech_adapter_load_failed');
      const active = this.loaded;
      const inferenceGeneration = this.activationGeneration;
      const audio = await this.engine.infer(active.adapter, semanticPayload);
      await this.assertCurrent(metadata, context, nowMs ?? Date.now());
      if (
        inferenceGeneration !== this.activationGeneration
        || this.loaded !== active
        || !sameAuthority(active.metadata, metadata)
      ) {
        audio.fill(0);
        throw new Error('speech_adapter_authority_changed');
      }
      if (!(audio instanceof Uint8Array) || audio.byteLength === 0) throw new Error('speech_adapter_quality_failed');
      return { mode: 'adapted', audio, reasonCode: null, adapterId: metadata.adapter_id };
    } catch (error) {
      const reasonCode = this.reasonCode(error);
      await this.unload();
      return this.fallback(semanticPayload, ordinaryAudio, reasonCode);
    }
  }

  async revoke(adapterId: string): Promise<void> {
    if (this.loaded?.metadata.adapter_id !== adapterId && this.pendingAdapterId !== adapterId) return;
    this.activationGeneration += 1;
    this.pendingAdapterId = null;
    if (this.loaded?.metadata.adapter_id === adapterId) await this.releaseLoaded();
  }

  async cleanupExpired(nowMs = Date.now()): Promise<boolean> {
    if (!this.loaded) return false;
    if (
      this.loaded.metadata.status !== 'approved'
      || nowMs >= Math.min(this.loaded.metadata.expires_at_ms, this.loaded.metadata.consent_expires_at_ms)
    ) {
      await this.unload();
      return true;
    }
    return false;
  }

  async unload(): Promise<void> {
    this.activationGeneration += 1;
    this.pendingAdapterId = null;
    await this.releaseLoaded();
  }

  private async releaseLoaded(): Promise<void> {
    const current = this.loaded;
    this.loaded = null;
    if (!current) return;
    await this.safeUnload(current.adapter);
    await this.engine.clearLocalArtifact(current.adapter.artifactSha256);
  }

  private async assertCurrent(
    metadata: SpeechAdapterMetadata,
    context: ReceiverSpeechContext,
    nowMs: number,
  ): Promise<void> {
    const expectedScopeDigest = await speechScopeDigest(
      context.pairId,
      context.direction,
      context.speakerDigest,
    );
    const checks: Array<[boolean, string]> = [
      [/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,191}$/.test(metadata.adapter_id), 'speech_adapter_id_invalid'],
      [metadata.status === 'approved', 'speech_adapter_not_approved'],
      [metadata.pair_id === context.pairId, 'speech_adapter_pair_mismatch'],
      [metadata.direction === context.direction, 'speech_adapter_direction_mismatch'],
      [metadata.speaker_digest === context.speakerDigest, 'speech_adapter_speaker_mismatch'],
      [metadata.scope_digest === context.scopeDigest && metadata.scope_digest === expectedScopeDigest,
        'speech_adapter_scope_mismatch'],
      [metadata.base_model_id === context.baseModelId, 'speech_adapter_base_model_mismatch'],
      [metadata.base_model_digest === context.baseModelDigest, 'speech_adapter_base_model_mismatch'],
      [metadata.consent_digest === context.consentDigest, 'speech_adapter_consent_mismatch'],
      [/^[0-9a-f]{64}$/.test(metadata.artifact_sha256), 'speech_adapter_artifact_digest_invalid'],
      [metadata.artifact_ref.startsWith('artifact://speech-adapters/') && !metadata.artifact_ref.split('/').includes('..'),
        'speech_adapter_artifact_ref_invalid'],
      [nowMs < metadata.consent_expires_at_ms, 'speech_adapter_consent_expired'],
      [nowMs < metadata.expires_at_ms, 'speech_adapter_expired'],
    ];
    const failure = checks.find(([passed]) => !passed);
    if (failure) throw new Error(failure[1]);
  }

  private async fallback(
    semanticPayload: Uint8Array,
    ordinaryAudio: Uint8Array | undefined,
    reasonCode: string,
  ): Promise<PersonalizedSpeechResult> {
    try {
      const audio = await this.engine.reconstructBase(semanticPayload);
      if (audio.byteLength) return { mode: 'base', audio, reasonCode, adapterId: null };
    } catch {
      // Ordinary encrypted call audio remains the final receiver-local path.
    }
    if (ordinaryAudio?.byteLength) {
      return { mode: 'ordinary_audio', audio: ordinaryAudio, reasonCode, adapterId: null };
    }
    return { mode: 'unavailable', audio: new Uint8Array(), reasonCode, adapterId: null };
  }

  private async safeUnload(adapter: ReceiverLoadedSpeechAdapter): Promise<void> {
    try {
      await this.engine.unload(adapter);
    } catch {
      // The service has already removed the handle from its reachable cache.
    }
  }

  private reasonCode(error: unknown): string {
    return error instanceof Error && /^[A-Za-z0-9_.:-]{1,128}$/.test(error.message)
      ? error.message
      : 'speech_adapter_runtime_failed';
  }
}

async function speechScopeDigest(
  pairId: string,
  direction: SpeechDirection,
  speakerDigest: string,
): Promise<string> {
  const canonical = JSON.stringify({ direction, pair_id: pairId, speaker_digest: speakerDigest });
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

function sameAuthority(before: SpeechAdapterMetadata, after: SpeechAdapterMetadata): boolean {
  return before.adapter_id === after.adapter_id
    && before.pair_id === after.pair_id
    && before.direction === after.direction
    && before.speaker_digest === after.speaker_digest
    && before.scope_digest === after.scope_digest
    && before.base_model_id === after.base_model_id
    && before.base_model_digest === after.base_model_digest
    && before.consent_digest === after.consent_digest
    && before.artifact_ref === after.artifact_ref
    && before.artifact_sha256 === after.artifact_sha256
    && before.expires_at_ms === after.expires_at_ms
    && before.consent_expires_at_ms === after.consent_expires_at_ms
    && before.registry_version === after.registry_version
    && before.status === after.status;
}

function sameContext(before: ReceiverSpeechContext, after: ReceiverSpeechContext): boolean {
  return before.pairId === after.pairId
    && before.direction === after.direction
    && before.speakerDigest === after.speakerDigest
    && before.scopeDigest === after.scopeDigest
    && before.baseModelId === after.baseModelId
    && before.baseModelDigest === after.baseModelDigest
    && before.consentDigest === after.consentDigest;
}
