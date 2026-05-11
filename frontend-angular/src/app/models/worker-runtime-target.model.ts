import { ModelScope, RuntimeKind, WorkerKind } from './context-access-policy.model';

export interface WorkerRuntimeCandidate {
  id: string;
  kind: WorkerKind | string;
  display_name: string;
  capabilities: string[];
  roles: string[];
  health: 'healthy' | 'degraded' | 'unhealthy' | 'unknown';
  validation_errors?: string[];
  risk_flags?: string[];
  last_seen?: string;
  version?: string;
}

export interface RuntimeTarget {
  id: string;
  kind: RuntimeKind | string;
  display_name: string;
  data_boundary: 'local' | 'private' | 'cloud' | 'external';
  allowed_capabilities: string[];
  denied_capabilities: string[];
  network_zone?: string;
  health: 'healthy' | 'degraded' | 'unhealthy' | 'unknown';
  tags?: string[];
  config?: { [key: string]: any };
}

export interface DestinationOption {
  id: string;
  kind: 'worker' | 'runtime' | 'provider' | 'model_scope';
  display_name: string;
  description?: string;
  risk_level?: 'low' | 'medium' | 'high' | 'critical';
}
