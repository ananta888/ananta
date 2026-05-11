import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { ApiBaseService } from './api-base.service';
import { 
  ContextAccessPolicy, 
  ContextBlockAccessDecision, 
  PolicyLintResult, 
  PolicyTemplate, 
  DestinationContextPreview,
  EffectivePolicyReadModel
} from '../models/context-access-policy.model';
import { ContextPolicyDiagnostics } from '../models/context-policy-diagnostics.model';

@Injectable({ providedIn: 'root' })
export class ContextAccessPolicyApiService extends ApiBaseService {

  listPolicies(baseUrl: string, projectId: string, token?: string): Observable<ContextAccessPolicy[]> {
    return this.core.get<ContextAccessPolicy[]>(`${baseUrl}/api/projects/${projectId}/context-access-policy`, baseUrl, token);
  }

  getPolicy(baseUrl: string, policyId: string, token?: string): Observable<ContextAccessPolicy> {
    return this.core.get<ContextAccessPolicy>(`${baseUrl}/api/context-access-policy/${policyId}`, baseUrl, token);
  }

  createDraft(baseUrl: string, projectId: string, policy: Partial<ContextAccessPolicy>, token?: string): Observable<ContextAccessPolicy> {
    return this.core.post<ContextAccessPolicy>(`${baseUrl}/api/projects/${projectId}/context-access-policy/draft`, policy, baseUrl, token);
  }

  updateDraft(baseUrl: string, policyId: string, patch: Partial<ContextAccessPolicy>, token?: string): Observable<ContextAccessPolicy> {
    return this.core.patch<ContextAccessPolicy>(`${baseUrl}/api/context-access-policy/${policyId}`, patch, baseUrl, token);
  }

  lintPolicy(baseUrl: string, policy: ContextAccessPolicy, token?: string): Observable<PolicyLintResult> {
    return this.core.post<PolicyLintResult>(`${baseUrl}/api/context-access-policy/lint`, policy, baseUrl, token);
  }

  activatePolicy(baseUrl: string, policyId: string, token?: string): Observable<ContextAccessPolicy> {
    return this.core.post<ContextAccessPolicy>(`${baseUrl}/api/context-access-policy/${policyId}/activate`, {}, baseUrl, token);
  }

  getEffectivePolicy(baseUrl: string, scope: string, token?: string): Observable<EffectivePolicyReadModel> {
    return this.core.get<EffectivePolicyReadModel>(`${baseUrl}/api/context-access-policy/effective?scope=${scope}`, baseUrl, token);
  }

  listTemplates(baseUrl: string, token?: string): Observable<PolicyTemplate[]> {
    return this.core.get<PolicyTemplate[]>(`${baseUrl}/api/context-access-policy/templates`, baseUrl, token);
  }

  applyTemplate(baseUrl: string, projectId: string, templateId: string, token?: string): Observable<ContextAccessPolicy> {
    return this.core.post<ContextAccessPolicy>(`${baseUrl}/api/projects/${projectId}/context-access-policy/apply-template`, { template_id: templateId }, baseUrl, token);
  }

  previewDecision(baseUrl: string, request: { source_metadata: any, destination: DestinationContextPreview }, token?: string): Observable<ContextBlockAccessDecision> {
    return this.core.post<ContextBlockAccessDecision>(`${baseUrl}/api/context-access-policy/preview`, request, baseUrl, token);
  }

  getDiagnostics(baseUrl: string, projectId: string, token?: string): Observable<ContextPolicyDiagnostics> {
    return this.core.get<ContextPolicyDiagnostics>(`${baseUrl}/api/projects/${projectId}/context-access-policy/diagnostics`, baseUrl, token);
  }
}
