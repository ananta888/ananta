import { Injectable, InjectionToken, inject } from '@angular/core';
import { Observable, catchError, defer, forkJoin, map, of, throwError } from 'rxjs';

import {
  AgentDirectoryService,
  normalizeHubOrigin,
} from '../../../services/agent-directory.service';
import { HubTasksApiClient } from '../../../services/hub-tasks-api.client';
import { SourcesService } from '../../../services/sources.service';
import { VisualProcessApiService } from '../../visual-process/visual-process-api.service';
import type { CaseFlowAgentBindingCatalog } from './caseflow-agent-graph.commands';

export type CaseFlowBindingResourceKind =
  | 'skill_profile'
  | 'agent_profile'
  | 'instruction_layer'
  | 'context_profile'
  | 'context_source'
  | 'model_profile'
  | 'model_role'
  | 'fallback_group';

/**
 * `ready` means the current authenticated response is authoritative, including
 * an authoritative empty list. `degraded` means loading or validation failed,
 * so the allowlist is empty. `disabled` means no such Hub catalog exists or a
 * configured subsystem explicitly reports that it is unavailable.
 */
export type CaseFlowBindingCatalogState = 'ready' | 'degraded' | 'disabled';

export type CaseFlowBindingCatalogReasonCode =
  | 'catalog_loaded'
  | 'catalog_not_configured'
  | 'catalog_not_implemented'
  | 'catalog_request_unauthorized'
  | 'catalog_request_forbidden'
  | 'catalog_request_failed'
  | 'catalog_response_invalid';

export interface CaseFlowBindingResourceAvailability {
  readonly state: CaseFlowBindingCatalogState;
  readonly reason_code: CaseFlowBindingCatalogReasonCode;
}

export interface CaseFlowAgentBindingCatalogReadModel {
  /** Intentional per-resource `disabled` states do not degrade this aggregate. */
  readonly state: CaseFlowBindingCatalogState;
  readonly catalog: CaseFlowAgentBindingCatalog;
  readonly availability: Readonly<
    Record<CaseFlowBindingResourceKind, CaseFlowBindingResourceAvailability>
  >;
}

export interface CaseFlowSkillProfileCatalogPort {
  listSkillProfiles(): Observable<unknown>;
}

export interface CaseFlowModelRoutingCatalogPort {
  listModelProfiles(): Observable<unknown>;
}

export interface CaseFlowInstructionProfileCatalogPort {
  listInstructionProfiles(): Observable<unknown>;
}

export interface CaseFlowContextSourceCatalogPort {
  listSources(): Observable<unknown>;
}

export const CASEFLOW_SKILL_PROFILE_CATALOG =
  new InjectionToken<CaseFlowSkillProfileCatalogPort>(
    'CASEFLOW_SKILL_PROFILE_CATALOG',
    {
      providedIn: 'root',
      factory: () => {
        const api = inject(VisualProcessApiService);
        return { listSkillProfiles: () => api.listSkillProfiles() };
      },
    },
  );

export const CASEFLOW_MODEL_ROUTING_CATALOG =
  new InjectionToken<CaseFlowModelRoutingCatalogPort>(
    'CASEFLOW_MODEL_ROUTING_CATALOG',
    {
      providedIn: 'root',
      factory: () => {
        const api = inject(VisualProcessApiService);
        return { listModelProfiles: () => api.listModelProfiles() };
      },
    },
  );

export const CASEFLOW_CONTEXT_SOURCE_CATALOG =
  new InjectionToken<CaseFlowContextSourceCatalogPort>(
    'CASEFLOW_CONTEXT_SOURCE_CATALOG',
    {
      providedIn: 'root',
      factory: () => {
        const sources = inject(SourcesService);
        return { listSources: () => sources.listSources() };
      },
    },
  );

/** Resolves the current Hub but never supplies a caller-controlled owner. */
@Injectable({ providedIn: 'root' })
export class CaseFlowInstructionProfileCatalogAdapter
implements CaseFlowInstructionProfileCatalogPort {
  private readonly directory = inject(AgentDirectoryService);
  private readonly client = inject(HubTasksApiClient);

  listInstructionProfiles(): Observable<unknown> {
    const agents = this.directory.list();
    const hub = agents.find(agent => agent.role === 'hub')
      ?? agents.find(agent => agent.name === 'hub');
    const hubUrl = normalizeHubOrigin(String(hub?.url ?? ''));
    if (!hubUrl) return throwError(() => ({ status: 0 }));

    // Omitting ownerUsername makes the Hub derive ownership from user auth.
    return this.client.listInstructionProfiles(hubUrl);
  }
}

export const CASEFLOW_INSTRUCTION_PROFILE_CATALOG =
  new InjectionToken<CaseFlowInstructionProfileCatalogPort>(
    'CASEFLOW_INSTRUCTION_PROFILE_CATALOG',
    {
      providedIn: 'root',
      factory: () => inject(CaseFlowInstructionProfileCatalogAdapter),
    },
  );

interface ResourceLoad<T> {
  readonly availability: CaseFlowBindingResourceAvailability;
  readonly value: T;
}

interface ModelCatalogIds {
  readonly profile_ids: readonly string[];
  readonly role_ids: readonly string[];
  readonly fallback_group_ids: readonly string[];
}

const DISABLED_NOT_IMPLEMENTED = availability(
  'disabled',
  'catalog_not_implemented',
);

@Injectable({ providedIn: 'root' })
export class CaseFlowAgentBindingCatalogService {
  private readonly skillProfiles = inject(CASEFLOW_SKILL_PROFILE_CATALOG);
  private readonly instructionProfiles = inject(CASEFLOW_INSTRUCTION_PROFILE_CATALOG);
  private readonly contextSources = inject(CASEFLOW_CONTEXT_SOURCE_CATALOG);
  private readonly modelRouting = inject(CASEFLOW_MODEL_ROUTING_CATALOG);

  /**
   * Loads independent catalogs in parallel while failing each resource closed.
   * No previous read model is retained, so a later authorization failure cannot
   * accidentally keep stale IDs selectable.
   */
  load(): Observable<CaseFlowAgentBindingCatalogReadModel> {
    return forkJoin({
      skills: guardedLoad(
        () => this.skillProfiles.listSkillProfiles(),
        normalizeSkillProfiles,
        EMPTY_IDS,
      ),
      instructions: guardedLoad(
        () => this.instructionProfiles.listInstructionProfiles(),
        normalizeInstructionProfiles,
        EMPTY_IDS,
      ),
      contexts: guardedLoad(
        () => this.contextSources.listSources(),
        normalizeContextSources,
        EMPTY_IDS,
      ),
      models: guardedLoad(
        () => this.modelRouting.listModelProfiles(),
        normalizeModelCatalog,
        EMPTY_MODEL_IDS,
      ),
    }).pipe(map(loads => buildReadModel(loads)));
  }
}

function buildReadModel(loads: {
  readonly skills: ResourceLoad<readonly string[]>;
  readonly instructions: ResourceLoad<readonly string[]>;
  readonly contexts: ResourceLoad<readonly string[]>;
  readonly models: ResourceLoad<ModelCatalogIds>;
}): CaseFlowAgentBindingCatalogReadModel {
  const availabilityByResource = Object.freeze({
    skill_profile: loads.skills.availability,
    agent_profile: DISABLED_NOT_IMPLEMENTED,
    instruction_layer: loads.instructions.availability,
    context_profile: DISABLED_NOT_IMPLEMENTED,
    context_source: loads.contexts.availability,
    model_profile: loads.models.availability,
    model_role: loads.models.availability,
    fallback_group: loads.models.availability,
  });

  const catalog: CaseFlowAgentBindingCatalog = Object.freeze({
    skill_profile_ids: readyValue(loads.skills, EMPTY_IDS),
    personality_resource_ids: Object.freeze({
      agent_profile: EMPTY_IDS,
      instruction_layer: readyValue(loads.instructions, EMPTY_IDS),
    }),
    context_resource_ids: Object.freeze({
      context_profile: EMPTY_IDS,
      context_source: readyValue(loads.contexts, EMPTY_IDS),
    }),
    model_profile_ids: readyValue(loads.models, EMPTY_MODEL_IDS).profile_ids,
    model_role_ids: readyValue(loads.models, EMPTY_MODEL_IDS).role_ids,
    fallback_group_ids: readyValue(loads.models, EMPTY_MODEL_IDS).fallback_group_ids,
  });

  return Object.freeze({
    state: aggregateState(Object.values(availabilityByResource)),
    catalog,
    availability: availabilityByResource,
  });
}

function guardedLoad<T>(
  request: () => Observable<unknown>,
  normalize: (value: unknown) => ResourceLoad<T>,
  emptyValue: T,
): Observable<ResourceLoad<T>> {
  return defer(request).pipe(
    map(normalize),
    catchError(error => of({
      availability: degradedAvailability(error),
      value: emptyValue,
    })),
  );
}

function normalizeSkillProfiles(value: unknown): ResourceLoad<readonly string[]> {
  return readyLoad(normalizeActiveIds(
    value,
    'id',
    false,
    'is_active',
    requireBoundedCatalogId,
  ));
}

function normalizeInstructionProfiles(value: unknown): ResourceLoad<readonly string[]> {
  return readyLoad(normalizeActiveIds(
    value,
    'id',
    true,
    'is_active',
    requireInstructionProfileId,
  ));
}

function normalizeContextSources(value: unknown): ResourceLoad<readonly string[]> {
  return readyLoad(normalizeActiveIds(
    value,
    'source_id',
    true,
    'enabled',
    requireSourceId,
  ));
}

function normalizeModelCatalog(value: unknown): ResourceLoad<ModelCatalogIds> {
  const raw = requireRecord(value);
  const status = raw['status'];
  if (status === 'not_configured') {
    return {
      availability: availability('disabled', 'catalog_not_configured'),
      value: EMPTY_MODEL_IDS,
    };
  }
  if (status !== 'loaded') throw new CatalogContractError();

  const profiles = requireArray(raw['profiles']);
  const profileIds: string[] = [];
  const roleIds: string[] = [];
  for (const item of profiles) {
    const profile = requireRecord(item);
    validateOptionalActivity(profile);
    if (isInactive(profile)) continue;
    profileIds.push(requireBoundedCatalogId(profile['profile_id']));
    roleIds.push(requireBoundedCatalogId(profile['model_role']));
  }

  const fallbackGroups = requireRecord(raw['fallback_groups']);
  const fallbackGroupIds = Object.keys(fallbackGroups).map(requireBoundedCatalogId);
  return readyLoad(Object.freeze({
    profile_ids: stableIds(profileIds),
    role_ids: stableIds(roleIds),
    fallback_group_ids: stableIds(fallbackGroupIds),
  }));
}

function normalizeActiveIds(
  value: unknown,
  idField: string,
  requireActive: boolean,
  activeField = 'is_active',
  normalizeId: (value: unknown) => string = requireBoundedCatalogId,
): readonly string[] {
  const ids: string[] = [];
  for (const item of requireArray(value)) {
    const raw = requireRecord(item);
    validateOptionalActivity(raw);
    if (requireActive && (raw[activeField] !== true || isInactive(raw))) continue;
    if (!requireActive && isInactive(raw)) continue;
    ids.push(normalizeId(raw[idField]));
  }
  return stableIds(ids);
}

function validateOptionalActivity(raw: Record<string, unknown>): void {
  for (const field of ['is_active', 'enabled']) {
    if (raw[field] !== undefined && typeof raw[field] !== 'boolean') {
      throw new CatalogContractError();
    }
  }
}

function isInactive(raw: Record<string, unknown>): boolean {
  return raw['is_active'] === false || raw['enabled'] === false;
}

function readyLoad<T>(value: T): ResourceLoad<T> {
  return {
    availability: availability('ready', 'catalog_loaded'),
    value,
  };
}

function readyValue<T>(load: ResourceLoad<T>, empty: T): T {
  return load.availability.state === 'ready' ? load.value : empty;
}

function aggregateState(
  resources: readonly CaseFlowBindingResourceAvailability[],
): CaseFlowBindingCatalogState {
  if (resources.some(resource => resource.state === 'degraded')) return 'degraded';
  if (resources.every(resource => resource.state === 'disabled')) return 'disabled';
  return 'ready';
}

function degradedAvailability(error: unknown): CaseFlowBindingResourceAvailability {
  if (error instanceof CatalogContractError) {
    return availability('degraded', 'catalog_response_invalid');
  }
  const status = readStatus(error);
  if (status === 401) return availability('degraded', 'catalog_request_unauthorized');
  if (status === 403) return availability('degraded', 'catalog_request_forbidden');
  return availability('degraded', 'catalog_request_failed');
}

function readStatus(error: unknown): number {
  if (!isRecord(error)) return 0;
  const status = error['status'];
  return typeof status === 'number' && Number.isFinite(status) ? status : 0;
}

function availability(
  state: CaseFlowBindingCatalogState,
  reasonCode: CaseFlowBindingCatalogReasonCode,
): CaseFlowBindingResourceAvailability {
  return Object.freeze({ state, reason_code: reasonCode });
}

function stableIds(values: readonly string[]): readonly string[] {
  return Object.freeze(Array.from(new Set(values)).sort((left, right) =>
    left < right ? -1 : left > right ? 1 : 0));
}

function requireSourceId(value: unknown): string {
  if (typeof value !== 'string' || value !== value.trim()
    || !/^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,191}$/.test(value)) {
    throw new CatalogContractError();
  }
  return value;
}

function requireInstructionProfileId(value: unknown): string {
  if (typeof value !== 'string'
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) {
    throw new CatalogContractError();
  }
  return value;
}

/**
 * Skill/model contracts allow human-readable IDs. Bound their projection and
 * reject control characters without imposing the stricter Source ID grammar.
 */
function requireBoundedCatalogId(value: unknown): string {
  if (typeof value !== 'string' || value !== value.trim()
    || value.length === 0 || value.length > 512
    || /[\u0000-\u001f\u007f-\u009f]/u.test(value)) {
    throw new CatalogContractError();
  }
  return value;
}

function requireArray(value: unknown): readonly unknown[] {
  if (!Array.isArray(value)) throw new CatalogContractError();
  return value;
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new CatalogContractError();
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

class CatalogContractError extends Error {}

const EMPTY_IDS: readonly string[] = Object.freeze([]);
const EMPTY_MODEL_IDS: ModelCatalogIds = Object.freeze({
  profile_ids: EMPTY_IDS,
  role_ids: EMPTY_IDS,
  fallback_group_ids: EMPTY_IDS,
});
