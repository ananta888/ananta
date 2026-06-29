import { CaseFlowCase } from '../caseflow.models';

export type JobStatus =
  | 'found'
  | 'interesting'
  | 'preparing'
  | 'applied'
  | 'waiting_response'
  | 'interview'
  | 'offer'
  | 'rejected'
  | 'archived';

export type RemotePolicy = 'remote' | 'onsite' | 'hybrid' | 'unknown';

export interface JobApplicationPayload {
  company_name: string;
  role_title: string;
  job_url?: string;
  source_name: string;
  location?: string;
  remote_policy: RemotePolicy;
  salary_min?: number;
  salary_max?: number;
  employment_type: string;
  contact_name?: string;
  contact_email?: string;
  applied_at?: string;
  tech_stack: string[];
  required_skills: string[];
  nice_to_have_skills?: string[];
  language_requirements?: string[];
}

export interface JobApplicationCase {
  case: CaseFlowCase;
  payload: JobApplicationPayload;
  fit_score?: JobFitScore;
}

export interface SubScore {
  score?: number;
  explanation: string;
}

export interface JobFitScore {
  id: string;
  case_id: string;
  source: 'ai' | 'manual';
  technical_fit?: SubScore;
  domain_fit?: SubScore;
  seniority_fit?: SubScore;
  location_fit?: SubScore;
  remote_fit?: SubScore;
  salary_fit?: SubScore;
  final_score?: number;
  manual_override?: number;
  manual_override_reason?: string;
  trace_id?: string;
  agent_run_id?: string;
}

export const JOB_STATUS_COLUMNS: JobStatus[] = [
  'found',
  'interesting',
  'preparing',
  'applied',
  'waiting_response',
  'interview',
  'offer',
  'rejected',
  'archived',
];

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  found: 'Gefunden',
  interesting: 'Interessant',
  preparing: 'In Vorbereitung',
  applied: 'Beworben',
  waiting_response: 'Warte auf Antwort',
  interview: 'Gespräch',
  offer: 'Angebot',
  rejected: 'Abgesagt',
  archived: 'Archiviert',
};
