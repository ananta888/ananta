import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class HubKnowledgeApiClient {
  private core = inject(HubApiCoreService);

  listCollections(baseUrl: string, token?: string): Observable<any[]> {
    return this.core.get<any[]>(`${baseUrl}/knowledge/collections`, baseUrl, token, true, 120000);
  }

  createCollection(baseUrl: string, payload: { name: string; description?: string }, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/knowledge/collections`, payload, baseUrl, token, false, 120000);
  }

  getCollection(baseUrl: string, collectionId: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/knowledge/collections/${collectionId}`, baseUrl, token, true, 120000);
  }

  indexCollection(baseUrl: string, collectionId: string, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/knowledge/collections/${collectionId}/index`, {}, baseUrl, token, false, 120000);
  }

  searchCollection(
    baseUrl: string,
    collectionId: string,
    payload: { query: string; top_k?: number },
    token?: string,
  ): Observable<any> {
    return this.core.post<any>(`${baseUrl}/knowledge/collections/${collectionId}/search`, payload, baseUrl, token, false, 120000);
  }
}
