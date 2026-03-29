import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class HubArtifactsApiClient {
  private core = inject(HubApiCoreService);

  listArtifacts(baseUrl: string, token?: string): Observable<any[]> {
    return this.core.get<any[]>(`${baseUrl}/artifacts`, baseUrl, token, true);
  }

  getArtifact(baseUrl: string, artifactId: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/artifacts/${artifactId}`, baseUrl, token, true);
  }

  extractArtifact(baseUrl: string, artifactId: string, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/artifacts/${artifactId}/extract`, {}, baseUrl, token);
  }

  indexArtifact(baseUrl: string, artifactId: string, body?: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/artifacts/${artifactId}/rag-index`, body || {}, baseUrl, token, false, 120000);
  }

  getArtifactRagStatus(baseUrl: string, artifactId: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/artifacts/${artifactId}/rag-status`, baseUrl, token, true, 120000);
  }

  getArtifactRagPreview(baseUrl: string, artifactId: string, limit = 5, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/artifacts/${artifactId}/rag-preview?limit=${limit}`, baseUrl, token, true, 120000);
  }

  uploadArtifact(baseUrl: string, file: File, collectionName?: string, token?: string): Observable<any> {
    const form = new FormData();
    form.append('file', file);
    if (collectionName?.trim()) {
      form.append('collection_name', collectionName.trim());
    }
    return this.core.post<any>(`${baseUrl}/artifacts/upload`, form, baseUrl, token, false, 120000);
  }
}
