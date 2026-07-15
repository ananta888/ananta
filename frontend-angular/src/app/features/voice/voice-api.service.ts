import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import {
  VoiceCapabilityStatus,
  VoiceConfiguration,
  VoiceConfigurationMutation,
  VoiceConfigurationQuery,
  VoiceConfigurationSaveResult,
  VoiceConfigurationSchema,
  VoiceConsent,
  VoiceConsentCategory,
  VoiceFineTuningExportTaskResult,
  VoiceLongRunCreatePayload,
  VoiceLongRunHeartbeatRequest,
  VoiceLongRunLease,
  VoiceLongRunResponse,
  VoiceLongRunSegmentUploadResponse,
  VoiceLongRunStopRequest,
  VoicePersonalizationExport,
  VoicePersonalizationImportPayload,
  VoicePersonalizationImportResult,
  VoicePersonalizationSnapshot,
  VoicePrivacyDeletionResult,
  VoiceResetResult,
  VoiceReview,
  VoiceReviewDecision,
  VoiceStreamCancelResponse,
  VoiceStreamChunkResponse,
  VoiceStreamCreateRequest,
  VoiceStreamCreateResponse,
  VoiceStreamFinalizeResponse,
  VoiceTranscriptionResult,
} from './voice.models';

const mutationHeaders = (idempotencyKey: string) => ({ 'Idempotency-Key': idempotencyKey });

function queryString(query: VoiceConfigurationQuery): string {
  const params = new URLSearchParams();
  if (query.profileId?.trim()) params.set('profile_id', query.profileId.trim());
  if (query.sessionId?.trim()) params.set('session_id', query.sessionId.trim());
  const value = params.toString();
  return value ? `?${value}` : '';
}

@Injectable({ providedIn: 'root' })
export class VoiceApiService {
  private readonly core = inject(HubApiCoreService);

  getCapabilities(hubUrl: string): Observable<VoiceCapabilityStatus> {
    return this.core.get<VoiceCapabilityStatus>(`${hubUrl}/v1/voice/capabilities`, hubUrl, undefined, true);
  }

  getConfigurationSchema(hubUrl: string): Observable<VoiceConfigurationSchema> {
    return this.core.get<VoiceConfigurationSchema | { schema: VoiceConfigurationSchema }>(
      `${hubUrl}/v1/voice/configuration/schema`, hubUrl, undefined, true,
    ).pipe(map((response) => 'schema' in response ? response.schema : response));
  }

  getConfiguration(hubUrl: string, query: VoiceConfigurationQuery = {}): Observable<VoiceConfiguration> {
    return this.core.get<{ configuration: VoiceConfiguration }>(
      `${hubUrl}/v1/voice/configuration${queryString(query)}`, hubUrl, undefined, false,
    ).pipe(map((response) => response.configuration));
  }

  saveConfiguration(
    hubUrl: string,
    mutation: VoiceConfigurationMutation,
    idempotencyKey: string,
  ): Observable<VoiceConfigurationSaveResult> {
    return this.core.request<{ configuration: VoiceConfigurationSaveResult }>(
      'PUT', `${hubUrl}/v1/voice/configuration`, hubUrl,
      { body: mutation, headers: mutationHeaders(idempotencyKey) },
    ).pipe(map((response) => response.configuration));
  }

  transcribe(
    hubUrl: string,
    payload: {
      file: Blob | File;
      fileName?: string;
      language?: string;
      profileId?: string;
      sessionId?: string;
      idempotencyKey?: string;
    },
  ): Observable<VoiceTranscriptionResult> {
    const form = new FormData();
    form.append('file', payload.file, payload.fileName || 'audio.webm');
    if (payload.language?.trim()) form.append('language', payload.language.trim());
    if (payload.profileId?.trim()) form.append('profile_id', payload.profileId.trim());
    if (payload.sessionId?.trim()) form.append('session_id', payload.sessionId.trim());
    return this.core.request<VoiceTranscriptionResult>('POST', `${hubUrl}/v1/voice/transcribe`, hubUrl, {
      body: form,
      timeoutMs: 120_000,
      headers: payload.idempotencyKey ? mutationHeaders(payload.idempotencyKey) : undefined,
    });
  }

  createStream(
    hubUrl: string,
    payload: VoiceStreamCreateRequest,
    idempotencyKey: string,
  ): Observable<VoiceStreamCreateResponse> {
    return this.core.request<VoiceStreamCreateResponse>(
      'POST', `${hubUrl}/v1/voice/streams`, hubUrl,
      { body: payload, headers: mutationHeaders(idempotencyKey) },
    );
  }

  pushStreamChunk(
    hubUrl: string,
    sessionId: string,
    chunkSequence: number,
    pcm16Chunk: ArrayBuffer | Blob,
  ): Observable<VoiceStreamChunkResponse> {
    return this.core.request<VoiceStreamChunkResponse>(
      'PUT',
      `${hubUrl}/v1/voice/streams/${encodeURIComponent(sessionId)}/chunks/${chunkSequence}`,
      hubUrl,
      {
        body: pcm16Chunk,
        headers: { 'Content-Type': 'audio/pcm;rate=16000;channels=1' },
        timeoutMs: 30_000,
      },
    );
  }

  finalizeStream(hubUrl: string, sessionId: string): Observable<VoiceStreamFinalizeResponse> {
    return this.core.request<VoiceStreamFinalizeResponse>(
      'POST', `${hubUrl}/v1/voice/streams/${encodeURIComponent(sessionId)}/finalize`, hubUrl,
      { timeoutMs: 120_000 },
    );
  }

  cancelStream(hubUrl: string, sessionId: string): Observable<VoiceStreamCancelResponse> {
    return this.core.request<VoiceStreamCancelResponse>(
      'DELETE', `${hubUrl}/v1/voice/streams/${encodeURIComponent(sessionId)}`, hubUrl,
    );
  }

  acquireLongRunLease(hubUrl: string, profileId: string): Observable<VoiceLongRunLease> {
    return this.core.request<VoiceLongRunLease>(
      'POST', `${hubUrl}/v1/voice/live-runs/lease`, hubUrl,
      { body: { profile_id: profileId } },
    );
  }

  createLongRun(
    hubUrl: string,
    payload: VoiceLongRunCreatePayload,
    idempotencyKey: string,
  ): Observable<VoiceLongRunResponse> {
    return this.core.request<VoiceLongRunResponse>(
      'POST', `${hubUrl}/v1/voice/live-runs`, hubUrl,
      { body: payload, headers: mutationHeaders(idempotencyKey) },
    );
  }

  getLongRun(
    hubUrl: string,
    runId: string,
    options: { includeText?: boolean } = {},
  ): Observable<VoiceLongRunResponse> {
    const query = options.includeText === false ? '?include_text=false' : '';
    return this.core.get<VoiceLongRunResponse>(
      `${hubUrl}/v1/voice/live-runs/${encodeURIComponent(runId)}${query}`, hubUrl, undefined, false,
    );
  }

  heartbeatLongRun(
    hubUrl: string,
    runId: string,
    payload: VoiceLongRunHeartbeatRequest,
  ): Observable<VoiceLongRunResponse> {
    return this.core.request<VoiceLongRunResponse>(
      'POST', `${hubUrl}/v1/voice/live-runs/${encodeURIComponent(runId)}/heartbeat`, hubUrl,
      { body: payload, timeoutMs: 30_000 },
    );
  }

  uploadLongRunSegment(
    hubUrl: string,
    runId: string,
    sequence: number,
    payload: {
      file: Blob;
      fileName: string;
      startedAtMs: number;
      endedAtMs: number;
      durationMs: number;
      overlapMilliseconds: number;
    },
    idempotencyKey: string,
  ): Observable<VoiceLongRunSegmentUploadResponse> {
    const form = new FormData();
    form.append('file', payload.file, payload.fileName);
    form.append('started_at_ms', String(payload.startedAtMs));
    form.append('ended_at_ms', String(payload.endedAtMs));
    form.append('duration_ms', String(payload.durationMs));
    form.append('overlap_milliseconds', String(payload.overlapMilliseconds));
    return this.core.request<VoiceLongRunSegmentUploadResponse>(
      'PUT',
      `${hubUrl}/v1/voice/live-runs/${encodeURIComponent(runId)}/segments/${sequence}`,
      hubUrl,
      {
        body: form,
        headers: mutationHeaders(idempotencyKey),
        timeoutMs: 300_000,
      },
    );
  }

  stopLongRun(
    hubUrl: string,
    runId: string,
    payload: VoiceLongRunStopRequest,
    idempotencyKey: string,
  ): Observable<VoiceLongRunResponse> {
    return this.core.request<VoiceLongRunResponse>(
      'POST', `${hubUrl}/v1/voice/live-runs/${encodeURIComponent(runId)}/stop`, hubUrl,
      { body: payload, headers: mutationHeaders(idempotencyKey), timeoutMs: 300_000 },
    );
  }

  createReview(
    hubUrl: string,
    payload: { profile_id: string; session_id?: string; result_ref: string; candidate_ids: string[] },
    idempotencyKey: string,
  ): Observable<VoiceReview> {
    return this.core.request<{ review: VoiceReview }>('POST', `${hubUrl}/v1/voice/reviews`, hubUrl, {
      body: payload,
      headers: mutationHeaders(idempotencyKey),
    }).pipe(map((response) => response.review));
  }

  getReview(hubUrl: string, reviewId: string): Observable<VoiceReview> {
    return this.core.get<{ review: VoiceReview }>(
      `${hubUrl}/v1/voice/reviews/${encodeURIComponent(reviewId)}`, hubUrl, undefined, false,
    ).pipe(map((response) => response.review));
  }

  decideReview(
    hubUrl: string,
    reviewId: string,
    payload: {
      decision: VoiceReviewDecision;
      expected_version: number;
      selected_candidate_id?: string;
      correction_text?: string;
    },
    idempotencyKey: string,
  ): Observable<VoiceReview> {
    return this.core.request<{ review: VoiceReview }>(
      'POST', `${hubUrl}/v1/voice/reviews/${encodeURIComponent(reviewId)}/decision`, hubUrl,
      { body: payload, headers: mutationHeaders(idempotencyKey) },
    ).pipe(map((response) => response.review));
  }

  getConsent(hubUrl: string, profileId: string): Observable<VoiceConsent> {
    return this.core.get<{ consent: VoiceConsent }>(
      `${hubUrl}/v1/voice/consents/${encodeURIComponent(profileId)}`, hubUrl, undefined, false,
    ).pipe(map((response) => response.consent));
  }

  setConsent(
    hubUrl: string,
    profileId: string,
    payload: { granted: boolean; categories: VoiceConsentCategory[]; retention_days: number },
    idempotencyKey: string,
  ): Observable<VoiceConsent> {
    return this.core.request<{ consent: VoiceConsent }>(
      'PUT', `${hubUrl}/v1/voice/consents/${encodeURIComponent(profileId)}`, hubUrl,
      { body: payload, headers: mutationHeaders(idempotencyKey) },
    ).pipe(map((response) => response.consent));
  }

  getPersonalizationSnapshot(hubUrl: string, profileId: string): Observable<VoicePersonalizationSnapshot> {
    return this.core.get<{ snapshot: VoicePersonalizationSnapshot }>(
      `${hubUrl}/v1/voice/personalization/${encodeURIComponent(profileId)}/snapshot`, hubUrl,
      undefined, false,
    ).pipe(map((response) => response.snapshot));
  }

  exportPersonalization(hubUrl: string, profileId: string): Observable<VoicePersonalizationExport> {
    return this.core.get<{ personalization: VoicePersonalizationExport }>(
      `${hubUrl}/v1/voice/personalization/${encodeURIComponent(profileId)}/export`, hubUrl,
      undefined, false,
    ).pipe(map((response) => response.personalization));
  }

  importPersonalization(
    hubUrl: string,
    profileId: string,
    payload: VoicePersonalizationImportPayload,
    idempotencyKey: string,
  ): Observable<VoicePersonalizationImportResult> {
    return this.core.request<{ import: VoicePersonalizationImportResult }>(
      'POST', `${hubUrl}/v1/voice/personalization/${encodeURIComponent(profileId)}/import`, hubUrl,
      { body: payload, headers: mutationHeaders(idempotencyKey) },
    ).pipe(map((response) => response.import));
  }

  addPersonalizationFeedback(
    hubUrl: string,
    payload: {
      profile_id: string;
      review_id: string;
      kind: 'vocabulary' | 'substitution' | 'preference' | 'negative';
      source_text?: string;
      target_text?: string;
      metadata?: Record<string, unknown>;
    },
    idempotencyKey: string,
  ): Observable<unknown> {
    return this.core.request<{ feedback: unknown }>(
      'POST', `${hubUrl}/v1/voice/personalization/feedback`, hubUrl,
      { body: payload, headers: mutationHeaders(idempotencyKey) },
    ).pipe(map((response) => response.feedback));
  }

  resetPersonalization(
    hubUrl: string,
    profileId: string,
    idempotencyKey: string,
  ): Observable<VoiceResetResult> {
    return this.core.request<{ reset: VoiceResetResult }>(
      'DELETE', `${hubUrl}/v1/voice/personalization/${encodeURIComponent(profileId)}`, hubUrl,
      { headers: mutationHeaders(idempotencyKey) },
    ).pipe(map((response) => response.reset));
  }

  deleteVoiceProfile(
    hubUrl: string,
    profileId: string,
    idempotencyKey: string,
  ): Observable<VoicePrivacyDeletionResult> {
    return this.core.request<{ deletion: VoicePrivacyDeletionResult }>(
      'DELETE', `${hubUrl}/v1/voice/privacy/${encodeURIComponent(profileId)}`, hubUrl,
      { body: { confirmed: true }, headers: mutationHeaders(idempotencyKey) },
    ).pipe(map((response) => response.deletion));
  }

  createFineTuningExportTask(
    hubUrl: string,
    profileId: string,
    payload: { confirmed: true; purpose: string; license: string },
    idempotencyKey: string,
  ): Observable<VoiceFineTuningExportTaskResult> {
    return this.core.request<VoiceFineTuningExportTaskResult>(
      'POST',
      `${hubUrl}/v1/voice/personalization/${encodeURIComponent(profileId)}/fine-tuning-export-tasks`,
      hubUrl,
      { body: payload, headers: mutationHeaders(idempotencyKey) },
    );
  }
}
