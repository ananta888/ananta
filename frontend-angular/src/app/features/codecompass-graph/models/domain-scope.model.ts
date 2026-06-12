// CCRDS-015: runtime domain scope models mirroring the hub API
// (GET /api/codecompass/domains, POST /api/codecompass/domain-scope/preview).

export interface DetectedDomain {
  domain_id: string;
  display_name: string;
  confidence: number;
  root_paths: string[];
  boundary_warnings: unknown[];
  has_descriptor: boolean;
}

export interface DomainListResponse {
  domains: DetectedDomain[];
  errors: string[];
  artifact_path: string;
  scope_enabled: boolean;
}

export interface DomainScopeViolation {
  kind: string;
  message: string;
  requested_path: string;
  matched_domain: string;
  allowed_paths: string[];
  severity: string;
}

export interface ResolvedDomainScopePreview {
  active: boolean;
  strict: boolean;
  selected_domain_ids: string[];
  allowed_read_paths: string[];
  allowed_write_paths: string[];
  source_domains: Array<{ domain_id: string; display_name: string; confidence: number; paths: string[] }>;
  warnings: string[];
  violations: DomainScopeViolation[];
  provenance: string[];
  preview_only?: boolean;
}
