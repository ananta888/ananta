import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class HubTemplatesApiClient {
  private core = inject(HubApiCoreService);
  listTemplates(baseUrl: string, token?: string): Observable<any[]> { return this.core.get<any[]>(`${baseUrl}/templates`, baseUrl, token, true); }
  createTemplate(baseUrl: string, tpl: any, token?: string): Observable<any> { return this.core.post(`${baseUrl}/templates`, tpl, baseUrl, token); }
  updateTemplate(baseUrl: string, id: string, patch: any, token?: string): Observable<any> { return this.core.patch(`${baseUrl}/templates/${id}`, patch, baseUrl, token); }
  deleteTemplate(baseUrl: string, id: string, token?: string): Observable<any> { return this.core.delete(`${baseUrl}/templates/${id}`, baseUrl, token); }
  getTemplateVariableRegistry(baseUrl: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/templates/variable-registry`, baseUrl, token, false);
  }
  getTemplateSampleContexts(baseUrl: string, token?: string): Observable<any> {
    return this.core.get<any>(`${baseUrl}/templates/sample-contexts`, baseUrl, token, false);
  }
  validateTemplate(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/templates/validate`, body, baseUrl, token);
  }
  previewTemplate(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/templates/preview`, body, baseUrl, token);
  }
  templateValidationDiagnostics(baseUrl: string, body: any, token?: string): Observable<any> {
    return this.core.post<any>(`${baseUrl}/templates/validation-diagnostics`, body, baseUrl, token);
  }
}
