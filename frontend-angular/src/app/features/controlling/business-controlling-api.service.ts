import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

export interface BusinessControllingStatus {
  readonly schema: string;
  readonly enabled: boolean;
  readonly read_only: boolean;
  readonly statistics_enabled: boolean;
  readonly explanations_enabled: boolean;
}

export interface BusinessControllingProfile {
  readonly profile_digest: string;
  readonly source_revision_id: string;
  readonly row_count: number;
  readonly columns: readonly { readonly header: string; readonly inferred_type: string }[];
}

export interface BusinessControllingMapping {
  readonly profile_digest: string;
  readonly confirmation_digest: string;
  readonly column_mapping: Readonly<Record<string, string>>;
}

export interface BusinessControllingFinding {
  readonly finding_id: string;
  readonly kind: 'deterministic_violation' | 'reconciliation_mismatch' | 'statistical_anomaly' | 'advisory_explanation';
  readonly severity: 'info' | 'low' | 'medium' | 'high' | 'critical';
  readonly dataset_version: string;
  readonly rule_version: string;
  readonly confidence: number | null;
  readonly evidence_digest: string;
  readonly disposition: 'open' | 'confirmed' | 'false_positive' | 'needs_data' | 'accepted_exception';
  readonly revision: number;
}

export interface BusinessControllingScope {
  readonly project_id: string;
}

@Injectable({ providedIn: 'root' })
export class BusinessControllingApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = '/api/v1/controlling';

  status(scope: BusinessControllingScope): Observable<BusinessControllingStatus> {
    return this.http.get<{ readonly status: BusinessControllingStatus }>(`${this.baseUrl}/status`, {
      params: new HttpParams().set('project_id', scope.project_id),
    }).pipe(map(response => response.status));
  }

  profileImport(
    scope: BusinessControllingScope,
    request: { readonly source_revision_id: string; readonly revision_digest: string; readonly source_format: 'csv' | 'xlsx' },
  ): Observable<BusinessControllingProfile> {
    return this.http.post<{ readonly profile: BusinessControllingProfile }>(
      `${this.baseUrl}/imports/profile`,
      { ...scope, ...request },
    ).pipe(map(response => response.profile));
  }

  confirmMapping(
    scope: BusinessControllingScope,
    profileDigest: string,
    columnMapping: Readonly<Record<string, string>>,
  ): Observable<BusinessControllingMapping> {
    return this.http.post<{ readonly mapping: BusinessControllingMapping }>(
      `${this.baseUrl}/mappings/confirm`,
      { ...scope, profile_digest: profileDigest, column_mapping: columnMapping },
    ).pipe(map(response => response.mapping));
  }

  startRun(
    scope: BusinessControllingScope,
    mappingConfirmationDigest: string,
    statisticsEnabled: boolean,
    explanationsEnabled: boolean,
    statisticalCatalogEntryId = '',
  ): Observable<{ readonly run_id: string; readonly status: string; readonly finding_count: number }> {
    return this.http.post<{ readonly run: { readonly run_id: string; readonly status: string; readonly finding_count: number } }>(
      `${this.baseUrl}/runs`,
      {
        ...scope,
        mapping_confirmation_digest: mappingConfirmationDigest,
        statistics_enabled: statisticsEnabled,
        explanations_enabled: explanationsEnabled,
        ...(statisticsEnabled ? { statistical_catalog_entry_id: statisticalCatalogEntryId } : {}),
        idempotency_key: `workbench-${mappingConfirmationDigest}`,
      },
    ).pipe(map(response => response.run));
  }

  findings(scope: BusinessControllingScope): Observable<readonly BusinessControllingFinding[]> {
    return this.http.get<{ readonly findings: readonly BusinessControllingFinding[] }>(`${this.baseUrl}/findings`, {
      params: new HttpParams().set('project_id', scope.project_id),
    }).pipe(map(response => response.findings));
  }

  setDisposition(
    scope: BusinessControllingScope,
    finding: BusinessControllingFinding,
    disposition: BusinessControllingFinding['disposition'],
  ): Observable<BusinessControllingFinding> {
    return this.http.post<{ readonly finding: BusinessControllingFinding }>(
      `${this.baseUrl}/findings/${encodeURIComponent(finding.finding_id)}/disposition`,
      { ...scope, disposition, expected_revision: finding.revision },
    ).pipe(map(response => response.finding));
  }

  export(scope: BusinessControllingScope): Observable<{ readonly report_digest: string; readonly content_redacted: boolean }> {
    return this.http.post<{ readonly report: { readonly report_digest: string; readonly content_redacted: boolean } }>(
      `${this.baseUrl}/exports`,
      scope,
    ).pipe(map(response => response.report));
  }
}
