import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';

export interface SemanticSpeechSourceCorrectionRequest {
  readonly hubUrl: string;
  readonly sessionId: string;
  readonly epoch: number;
  readonly turnId: string;
  readonly finalRevision: number;
  readonly consentVersion: number;
  readonly consentId: string;
  readonly consentDigest: string;
  readonly consentRevocationEpoch: number;
  readonly contractDigest: string;
  readonly sourceDigest: string;
  readonly sourceExpiresAtMs: number;
  readonly deadlineAtMs: number;
  readonly finalText: string;
  readonly sourceAudio: Uint8Array;
  readonly language?: string;
}

export interface SemanticSpeechSourceCorrectionResponse {
  readonly session_id: string;
  readonly epoch: number;
  readonly turn_id: string;
  readonly revision: number;
  readonly supersedes_revision: number;
  readonly text: string;
  readonly authority: 'corrected' | 'final' | 'correction_failed' | 'missing_source';
  readonly reason_code: string;
  readonly source_digest: string;
  readonly correction_attempted: boolean;
  readonly operations: readonly Readonly<{
    kind: 'equal' | 'insert' | 'delete' | 'replace';
    reference_text: string;
    candidate_text: string;
    candidate_id: string;
    start_ms: number | null;
    end_ms: number | null;
    confidence: number | null;
    alignment_method: 'time_v1' | 'unicode_text_v1';
  }>[];
  readonly task_id: string;
  readonly idempotent_replay: boolean;
}

@Injectable({ providedIn: 'root' })
export class SemanticSpeechSourceCorrectionApiService {
  private readonly core = inject(HubApiCoreService);

  correct(request: SemanticSpeechSourceCorrectionRequest): Observable<SemanticSpeechSourceCorrectionResponse> {
    const form = new FormData();
    const sourceCopy = new Uint8Array(request.sourceAudio.byteLength);
    sourceCopy.set(request.sourceAudio);
    let sourceBlob: Blob;
    try {
      sourceBlob = new Blob([sourceCopy.buffer], { type: 'audio/wav' });
    } finally {
      sourceCopy.fill(0);
    }
    form.append('file', sourceBlob, `${request.turnId}.wav`);
    form.append('session_id', request.sessionId);
    form.append('epoch', String(request.epoch));
    form.append('turn_id', request.turnId);
    form.append('final_revision', String(request.finalRevision));
    form.append('consent_version', String(request.consentVersion));
    form.append('consent_id', request.consentId);
    form.append('consent_digest', request.consentDigest);
    form.append('consent_revocation_epoch', String(request.consentRevocationEpoch));
    form.append('contract_digest', request.contractDigest);
    form.append('source_digest', request.sourceDigest);
    form.append('source_expires_at_ms', String(request.sourceExpiresAtMs));
    form.append('deadline_at_ms', String(request.deadlineAtMs));
    form.append('deadline_seconds', String(Math.max(.1, (request.deadlineAtMs - Date.now()) / 1_000)));
    form.append('final_text', request.finalText);
    if (request.language?.trim()) form.append('language', request.language.trim());
    const idempotencyKey = [
      'semantic-source-correction', request.sessionId, request.epoch, request.turnId,
      request.finalRevision, request.sourceDigest,
    ].join(':');
    return this.core.request<SemanticSpeechSourceCorrectionResponse>(
      'POST', `${request.hubUrl}/v1/voice/source-corrections`, request.hubUrl,
      {
        body: form,
        headers: { 'Idempotency-Key': idempotencyKey },
        timeoutMs: Math.max(1_000, Math.min(30_000, request.deadlineAtMs - Date.now())),
      },
    );
  }
}
