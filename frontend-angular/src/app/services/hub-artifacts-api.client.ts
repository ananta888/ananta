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

  uploadArtifact(baseUrl: string, file: File, collectionName?: string, token?: string): Observable<any> {
    const form = new FormData();
    form.append('file', file);
    if (collectionName?.trim()) {
      form.append('collection_name', collectionName.trim());
    }
    return this.core.post<any>(`${baseUrl}/artifacts/upload`, form, baseUrl, token, false, 120000);
  }
}
