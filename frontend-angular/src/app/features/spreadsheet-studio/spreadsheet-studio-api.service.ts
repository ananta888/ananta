import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import {
  SpreadsheetDocument,
  SpreadsheetProposalResult,
  WorkbookSnapshot,
} from './spreadsheet-studio.models';

@Injectable({ providedIn: 'root' })
export class SpreadsheetStudioApiService {
  private readonly core = inject(HubApiCoreService);

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

  execute(hubUrl: string, proposal: Record<string, unknown>): Observable<SpreadsheetProposalResult> {
    return this.core.post<SpreadsheetProposalResult>(
      `${this.endpoint(hubUrl)}/proposals/execute`, proposal, hubUrl,
    );
  }

  private endpoint(hubUrl: string): string {
    return `${hubUrl.replace(/\/+$/, '')}/api/spreadsheet-studio`;
  }
}
