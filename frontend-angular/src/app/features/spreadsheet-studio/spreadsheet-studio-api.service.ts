import { HttpResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import {
  SpreadsheetDataset,
  SpreadsheetDocument,
  SpreadsheetFeedbackEvent,
  SpreadsheetInferenceProposal,
  SpreadsheetPrivacyPreview,
  SpreadsheetProposalResult,
  SpreadsheetStudioCapabilities,
  SpreadsheetTrainingAdmission,
  SpreadsheetTrainingConsent,
  WorkbookSnapshot,
} from './spreadsheet-studio.models';

@Injectable({ providedIn: 'root' })
export class SpreadsheetStudioApiService {
  private readonly core = inject(HubApiCoreService);

  capabilities(hubUrl: string): Observable<SpreadsheetStudioCapabilities> {
    return this.core.get<SpreadsheetStudioCapabilities>(`${this.endpoint(hubUrl)}/capabilities`, hubUrl);
  }

  list(hubUrl: string): Observable<{ items: SpreadsheetDocument[]; limit: number }> {
    return this.core.get<{ items: SpreadsheetDocument[]; limit: number }>(
      `${this.endpoint(hubUrl)}/documents`, hubUrl,
    );
  }

  create(hubUrl: string, title: string, snapshot: WorkbookSnapshot): Observable<SpreadsheetDocument> {
    return this.core.post<SpreadsheetDocument>(
      `${this.endpoint(hubUrl)}/documents`, { title, snapshot }, hubUrl,
    );
  }

  importDocument(hubUrl: string, file: File, title?: string): Observable<SpreadsheetDocument> {
    const form = new FormData();
    form.append('file', file, file.name);
    if (title?.trim()) form.append('title', title.trim());
    return this.core.request<SpreadsheetDocument>('POST', `${this.endpoint(hubUrl)}/documents/import`, hubUrl, {
      body: form,
      timeoutMs: 120_000,
    });
  }

  downloadOriginal(hubUrl: string, documentId: string): Observable<HttpResponse<Blob>> {
    return this.core.requestBlob(`${this.endpoint(hubUrl)}/documents/${encodeURIComponent(documentId)}/original`, hubUrl);
  }

  downloadPublished(hubUrl: string, documentId: string): Observable<HttpResponse<Blob>> {
    return this.core.requestBlob(
      `${this.endpoint(hubUrl)}/documents/${encodeURIComponent(documentId)}/published`, hubUrl,
    );
  }

  execute(hubUrl: string, proposal: Record<string, unknown>): Observable<SpreadsheetProposalResult> {
    return this.core.post<SpreadsheetProposalResult>(
      `${this.endpoint(hubUrl)}/proposals/execute`, proposal, hubUrl, undefined, false, 120_000,
    );
  }

  recordFeedback(hubUrl: string, command: Record<string, unknown>): Observable<SpreadsheetFeedbackEvent> {
    return this.core.post<SpreadsheetFeedbackEvent>(`${this.endpoint(hubUrl)}/feedback`, command, hubUrl);
  }

  privacyPreview(hubUrl: string, eventId: string): Observable<SpreadsheetPrivacyPreview> {
    return this.core.get<SpreadsheetPrivacyPreview>(
      `${this.endpoint(hubUrl)}/feedback/${encodeURIComponent(eventId)}/privacy-preview`, hubUrl,
    );
  }

  grantConsent(hubUrl: string, command: Record<string, unknown>): Observable<SpreadsheetTrainingConsent> {
    return this.core.post<SpreadsheetTrainingConsent>(`${this.endpoint(hubUrl)}/consents`, command, hubUrl);
  }

  revokeConsent(hubUrl: string, consentId: string, expectedVersion: number): Observable<SpreadsheetTrainingConsent> {
    return this.core.post<SpreadsheetTrainingConsent>(
      `${this.endpoint(hubUrl)}/consents/${encodeURIComponent(consentId)}/revoke`,
      { expected_version: expectedVersion }, hubUrl,
    );
  }

  materializeDataset(hubUrl: string, command: Record<string, unknown>): Observable<SpreadsheetDataset> {
    return this.core.post<SpreadsheetDataset>(`${this.endpoint(hubUrl)}/datasets/materialize`, command, hubUrl);
  }

  infer(hubUrl: string, command: Record<string, unknown>): Observable<SpreadsheetInferenceProposal> {
    return this.core.post<SpreadsheetInferenceProposal>(
      `${this.endpoint(hubUrl)}/inference/proposals`, command, hubUrl, undefined, false, 120_000,
    );
  }

  startTraining(
    hubUrl: string,
    datasetId: string,
    command: Record<string, unknown>,
    idempotencyKey: string,
  ): Observable<SpreadsheetTrainingAdmission> {
    return this.core.request<SpreadsheetTrainingAdmission>(
      'POST', `${this.endpoint(hubUrl)}/datasets/${encodeURIComponent(datasetId)}/training`, hubUrl,
      { body: command, headers: { 'Idempotency-Key': idempotencyKey }, timeoutMs: 120_000 },
    );
  }

  private endpoint(hubUrl: string): string {
    return `${hubUrl.replace(/\/+$/, '')}/api/spreadsheet-studio`;
  }
}
