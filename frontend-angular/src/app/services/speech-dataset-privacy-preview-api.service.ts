import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';

export interface SpeechDatasetRawAudioPreview {
  readonly authorized: boolean;
  readonly refs: readonly string[];
}

export interface SpeechDatasetPrivacyPreview {
  readonly schema: 'ananta.speech-dataset-privacy-preview.v1';
  readonly datasetId: string;
  readonly manifestDigest: string;
  readonly recordCount: number;
  readonly totalDurationMs: number;
  readonly dataClassCounts: Readonly<Record<string, number>>;
  readonly contributorScopes: Readonly<Record<string, number>>;
  readonly grantRefCounts: Readonly<Record<string, number>>;
  readonly quarantineCount: number;
  readonly scanFindings: Readonly<Record<string, number>>;
  readonly rawAudioPreview: SpeechDatasetRawAudioPreview;
}

@Injectable({ providedIn: 'root' })
export class SpeechDatasetPrivacyPreviewApiService {
  private readonly core = inject(HubApiCoreService);

  aggregate(hubUrl: string, manifestDigest: string): Observable<SpeechDatasetPrivacyPreview> {
    return this.get(hubUrl, manifestDigest, false, null);
  }

  withRawAudioGrant(
    hubUrl: string,
    manifestDigest: string,
    previewGrantRef: string,
  ): Observable<SpeechDatasetPrivacyPreview> {
    return this.get(hubUrl, manifestDigest, true, grant(previewGrantRef));
  }

  private get(
    hubUrl: string,
    manifestDigest: string,
    includeRawAudio: boolean,
    previewGrantRef: string | null,
  ): Observable<SpeechDatasetPrivacyPreview> {
    const base = normalizeHubUrl(hubUrl);
    const query = includeRawAudio ? '?include_raw_audio=true' : '';
    const headers = previewGrantRef ? { 'X-Speech-Preview-Grant': previewGrantRef } : undefined;
    return this.core.request<unknown>(
      'GET',
      `${base}/v1/semantic-media/privacy/speech-datasets/${digest(manifestDigest)}/preview${query}`,
      base,
      { headers },
    ).pipe(map(parseSpeechDatasetPrivacyPreviewResponse));
  }
}

export function parseSpeechDatasetPrivacyPreviewResponse(value: unknown): SpeechDatasetPrivacyPreview {
  const envelope = closedRecord(value, ['ok', 'data'], 'speech_preview_envelope_invalid');
  if (envelope['ok'] !== true) fail('speech_preview_envelope_invalid');
  const row = closedRecord(envelope['data'], [
    'schema', 'dataset_id', 'manifest_digest', 'record_count', 'total_duration_ms',
    'data_class_counts', 'contributor_scopes', 'grant_ref_counts', 'quarantine_count',
    'scan_findings', 'raw_audio_preview',
  ], 'speech_preview_response_invalid');
  if (row['schema'] !== 'ananta.speech-dataset-privacy-preview.v1') fail('speech_preview_schema_invalid');
  const raw = closedRecord(row['raw_audio_preview'], ['authorized', 'refs'], 'speech_preview_raw_invalid');
  if (typeof raw['authorized'] !== 'boolean' || !Array.isArray(raw['refs']) || raw['refs'].length > 100) {
    fail('speech_preview_raw_invalid');
  }
  const refs = raw['refs'].map(rawRef);
  if (!raw['authorized'] && refs.length) fail('speech_preview_raw_invalid');
  return Object.freeze({
    schema: 'ananta.speech-dataset-privacy-preview.v1',
    datasetId: identifier(row['dataset_id']),
    manifestDigest: digest(row['manifest_digest']),
    recordCount: boundedInteger(row['record_count'], 0, 10_000),
    totalDurationMs: boundedInteger(row['total_duration_ms'], 0, Number.MAX_SAFE_INTEGER),
    dataClassCounts: counts(row['data_class_counts']),
    contributorScopes: counts(row['contributor_scopes']),
    grantRefCounts: counts(row['grant_ref_counts']),
    quarantineCount: boundedInteger(row['quarantine_count'], 0, 10_000),
    scanFindings: counts(row['scan_findings']),
    rawAudioPreview: Object.freeze({ authorized: raw['authorized'], refs: Object.freeze(refs) }),
  });
}

function counts(value: unknown): Readonly<Record<string, number>> {
  const row = record(value, 'speech_preview_counts_invalid');
  if (Object.keys(row).length > 10_000) fail('speech_preview_counts_invalid');
  return Object.freeze(Object.fromEntries(Object.entries(row).map(([key, count]) => [
    identifier(key), boundedInteger(count, 0, 10_000),
  ])));
}

function rawRef(value: unknown): string {
  const rendered = String(value ?? '');
  if (!/^artifact:\/\/speech-preview\/[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$/.test(rendered)
      || rendered.split('/').includes('..')) fail('speech_preview_raw_ref_invalid');
  return rendered;
}

function normalizeHubUrl(value: string): string {
  const normalized = String(value || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^\s]+$/.test(normalized)) fail('speech_preview_hub_url_invalid');
  return normalized;
}

function closedRecord(value: unknown, fields: readonly string[], reason: string): Record<string, unknown> {
  const row = record(value, reason);
  if (Object.keys(row).some(key => !fields.includes(key)) || fields.some(key => !(key in row))) fail(reason);
  return row;
}

function record(value: unknown, reason: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(reason);
  return value as Record<string, unknown>;
}

function identifier(value: unknown): string {
  const rendered = String(value ?? '');
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,159}$/.test(rendered)) fail('speech_preview_identifier_invalid');
  return rendered;
}

function digest(value: unknown): string {
  const rendered = String(value ?? '');
  if (!/^[0-9a-f]{64}$/.test(rendered)) fail('speech_preview_digest_invalid');
  return rendered;
}

function grant(value: unknown): string {
  const rendered = String(value ?? '').trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9_.:@-]{7,255}$/.test(rendered)) fail('speech_preview_grant_invalid');
  return rendered;
}

function boundedInteger(value: unknown, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || Number(value) < minimum || Number(value) > maximum) {
    fail('speech_preview_integer_invalid');
  }
  return Number(value);
}

function fail(reason: string): never { throw new Error(reason); }
