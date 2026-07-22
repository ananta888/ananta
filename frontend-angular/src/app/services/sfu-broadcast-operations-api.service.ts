import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import { SFU_ALLOW_INSECURE_LOCALHOST_TRANSPORT, isAllowedSfuEndpoint } from './sfu-secure-endpoint.policy';

export type SfuDiagnosticAtom = string | number | boolean | null;

export interface SfuBroadcastOperationsQuery {
  readonly tenantRef?: string;
  readonly region?: string;
  readonly roomRef?: string;
  readonly receiverRef?: string;
  readonly pageSize?: number;
  readonly cursor?: string;
}

export interface SfuBroadcastOperationItem {
  readonly roomDiagnosticRef: string | null;
  readonly receiverDiagnosticRef: string | null;
  readonly regionDiagnosticRef: string | null;
  readonly groupStatus: SfuDiagnosticAtom;
  readonly routeStatus: SfuDiagnosticAtom;
  readonly epochClass: SfuDiagnosticAtom;
  readonly topology: SfuDiagnosticAtom;
  readonly health: SfuDiagnosticAtom;
  readonly layers: Readonly<{
    requested: SfuDiagnosticAtom;
    allowed: SfuDiagnosticAtom;
    effective: SfuDiagnosticAtom;
    distribution: Readonly<Record<string, number>>;
  }>;
  readonly queue: Readonly<{ depthBucket: SfuDiagnosticAtom; dropReason: SfuDiagnosticAtom }>;
  readonly traffic: Readonly<{
    ingressBucket: SfuDiagnosticAtom;
    egressBucket: SfuDiagnosticAtom;
    turnBucket: SfuDiagnosticAtom;
  }>;
  readonly rekeyStatus: SfuDiagnosticAtom;
  readonly failoverStatus: SfuDiagnosticAtom;
  readonly capacityProfile: SfuDiagnosticAtom;
  readonly gateState: SfuDiagnosticAtom;
}

export interface SfuBroadcastOperationsPage {
  readonly reasonCode: 'sfu_operations_snapshot_read';
  readonly snapshotRef: string;
  readonly items: readonly SfuBroadcastOperationItem[];
  readonly nextCursor: string | null;
}

export type SfuBroadcastCommandName = 'start' | 'stop' | 'set_preferences';
export type SfuBroadcastQualityPreference = 'auto' | 'low' | 'medium' | 'high';

export interface SfuBroadcastCommandRequest {
  readonly roomRef: string;
  readonly command: SfuBroadcastCommandName;
  readonly expectedVersion: number;
  readonly confirmed: true;
  readonly options: Readonly<{
    dataSaver?: boolean;
    audioOnly?: boolean;
    qualityPreference?: SfuBroadcastQualityPreference;
  }>;
}

export interface SfuBroadcastCommandResult {
  readonly ok: boolean;
  readonly accepted: boolean;
  readonly effectiveVersion: number;
  readonly state: string;
  readonly reasonCode: string;
  readonly commandRef: string;
  readonly replayed: boolean;
}

@Injectable({ providedIn: 'root' })
export class SfuBroadcastOperationsApiService {
  private readonly core = inject(HubApiCoreService);
  private readonly allowInsecureLocalhost = inject(SFU_ALLOW_INSECURE_LOCALHOST_TRANSPORT);

  read(
    hubUrl: string,
    query: Readonly<SfuBroadcastOperationsQuery>,
  ): Observable<SfuBroadcastOperationsPage> {
    const base = normalizeBase(hubUrl, this.allowInsecureLocalhost);
    const params = new URLSearchParams();
    appendIdentifier(params, 'tenant_ref', query.tenantRef);
    appendIdentifier(params, 'region', query.region);
    appendIdentifier(params, 'room_ref', query.roomRef);
    appendIdentifier(params, 'receiver_ref', query.receiverRef);
    const pageSize = query.pageSize ?? 25;
    if (!Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 100) {
      throw new Error('sfu_operations_page_size_invalid');
    }
    params.set('page_size', String(pageSize));
    if (query.cursor !== undefined) params.set('cursor', cursor(query.cursor));
    return this.core.request<unknown>(
      'GET',
      `${base}/v1/semantic-media/sfu/broadcast/operations?${params.toString()}`,
      base,
      { timeoutMs: 8_000 },
    ).pipe(map(parsePage));
  }

  command(
    hubUrl: string,
    request: Readonly<SfuBroadcastCommandRequest>,
    idempotencyKey: string,
  ): Observable<SfuBroadcastCommandResult> {
    const base = normalizeBase(hubUrl, this.allowInsecureLocalhost);
    const body = commandBody(request);
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$/.test(idempotencyKey)) {
      throw new Error('sfu_command_idempotency_key_invalid');
    }
    return this.core.request<unknown>(
      'POST',
      `${base}/v1/semantic-media/sfu/broadcast/commands`,
      base,
      {
        body,
        headers: { 'Idempotency-Key': idempotencyKey },
        timeoutMs: 8_000,
      },
    ).pipe(map(parseCommandResult));
  }
}

function commandBody(value: Readonly<SfuBroadcastCommandRequest>): Record<string, unknown> {
  if (!identifier(value.roomRef) || !['start', 'stop', 'set_preferences'].includes(value.command)
      || !Number.isSafeInteger(value.expectedVersion) || value.expectedVersion < 0
      || value.confirmed !== true || !value.options || typeof value.options !== 'object') {
    throw new Error('sfu_command_payload_invalid');
  }
  const allowedOptionKeys = ['dataSaver', 'audioOnly', 'qualityPreference'];
  if (Object.keys(value.options).some(key => !allowedOptionKeys.includes(key))) {
    throw new Error('sfu_command_payload_invalid');
  }
  const options: Record<string, unknown> = {};
  if (value.command === 'stop' && Object.keys(value.options).length) {
    throw new Error('sfu_command_payload_invalid');
  }
  if (value.options.dataSaver !== undefined) options['data_saver'] = boolean(value.options.dataSaver);
  if (value.options.audioOnly !== undefined) options['audio_only'] = boolean(value.options.audioOnly);
  if (value.options.qualityPreference !== undefined) {
    if (!['auto', 'low', 'medium', 'high'].includes(value.options.qualityPreference)) {
      throw new Error('sfu_command_payload_invalid');
    }
    options['quality_preference'] = value.options.qualityPreference;
  }
  return Object.freeze({
    room_ref: value.roomRef,
    command: value.command,
    expected_version: value.expectedVersion,
    confirmed: true,
    options: Object.freeze(options),
  });
}

function parsePage(raw: unknown): SfuBroadcastOperationsPage {
  const value = closed(raw, ['ok', 'reason_code', 'snapshot_ref', 'items', 'next_cursor']);
  if (value['ok'] !== true || value['reason_code'] !== 'sfu_operations_snapshot_read'
      || !Array.isArray(value['items']) || value['items'].length > 100) {
    throw new Error('sfu_operations_response_invalid');
  }
  return Object.freeze({
    reasonCode: 'sfu_operations_snapshot_read' as const,
    snapshotRef: diagnosticRef(value['snapshot_ref'], false)!,
    items: Object.freeze(value['items'].map(parseItem)),
    nextCursor: value['next_cursor'] === null ? null : cursor(value['next_cursor']),
  });
}

function parseItem(raw: unknown): SfuBroadcastOperationItem {
  const value = closed(raw, [
    'room_diagnostic_ref', 'receiver_diagnostic_ref', 'region_diagnostic_ref',
    'group_status', 'route_status', 'epoch_class', 'topology', 'health', 'layers',
    'queue', 'traffic', 'rekey_status', 'failover_status', 'capacity_profile', 'gate_state',
  ]);
  const layers = closed(value['layers'], ['requested', 'allowed', 'effective', 'distribution']);
  const queue = closed(value['queue'], ['depth_bucket', 'drop_reason']);
  const traffic = closed(value['traffic'], ['ingress_bucket', 'egress_bucket', 'turn_bucket']);
  return Object.freeze({
    roomDiagnosticRef: diagnosticRef(value['room_diagnostic_ref'], true),
    receiverDiagnosticRef: diagnosticRef(value['receiver_diagnostic_ref'], true),
    regionDiagnosticRef: diagnosticRef(value['region_diagnostic_ref'], true),
    groupStatus: atom(value['group_status']),
    routeStatus: atom(value['route_status']),
    epochClass: atom(value['epoch_class']),
    topology: atom(value['topology']),
    health: atom(value['health']),
    layers: Object.freeze({
      requested: atom(layers['requested']),
      allowed: atom(layers['allowed']),
      effective: atom(layers['effective']),
      distribution: distribution(layers['distribution']),
    }),
    queue: Object.freeze({ depthBucket: atom(queue['depth_bucket']), dropReason: atom(queue['drop_reason']) }),
    traffic: Object.freeze({
      ingressBucket: atom(traffic['ingress_bucket']),
      egressBucket: atom(traffic['egress_bucket']),
      turnBucket: atom(traffic['turn_bucket']),
    }),
    rekeyStatus: atom(value['rekey_status']),
    failoverStatus: atom(value['failover_status']),
    capacityProfile: atom(value['capacity_profile']),
    gateState: atom(value['gate_state']),
  });
}

function parseCommandResult(raw: unknown): SfuBroadcastCommandResult {
  const value = closed(raw, [
    'ok', 'accepted', 'effective_version', 'state', 'reason_code', 'command_ref', 'replayed',
  ]);
  if (typeof value['ok'] !== 'boolean' || typeof value['accepted'] !== 'boolean'
      || !Number.isSafeInteger(value['effective_version']) || (value['effective_version'] as number) < 0
      || typeof value['replayed'] !== 'boolean') {
    throw new Error('sfu_command_response_invalid');
  }
  return Object.freeze({
    ok: value['ok'],
    accepted: value['accepted'],
    effectiveVersion: value['effective_version'] as number,
    state: status(value['state'], 'sfu_command_response_invalid'),
    reasonCode: reason(value['reason_code']),
    commandRef: diagnosticRef(value['command_ref'], false)!,
    replayed: value['replayed'],
  });
}

function closed(raw: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('sfu_operations_response_invalid');
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).length !== keys.length || Object.keys(value).some(key => !keys.includes(key))
      || keys.some(key => !(key in value))) throw new Error('sfu_operations_response_invalid');
  return value;
}

function atom(value: unknown): SfuDiagnosticAtom {
  if (value === null) return null;
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1_000_000_000) return value;
  return status(value, 'sfu_operations_response_invalid');
}

function distribution(raw: unknown): Readonly<Record<string, number>> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('sfu_operations_response_invalid');
  const value = raw as Record<string, unknown>;
  const keys = ['none', 'low', 'medium', 'high', 'unknown', 'suppressed'];
  if (Object.keys(value).length > keys.length || Object.keys(value).some(key => !keys.includes(key))) {
    throw new Error('sfu_operations_response_invalid');
  }
  const result: Record<string, number> = {};
  for (const [key, count] of Object.entries(value)) {
    if (!Number.isSafeInteger(count) || (count as number) < 0 || (count as number) > 250) {
      throw new Error('sfu_operations_response_invalid');
    }
    result[key] = count as number;
  }
  return Object.freeze(result);
}

function appendIdentifier(params: URLSearchParams, key: string, value: string | undefined): void {
  if (value === undefined || value === '') return;
  if (!identifier(value)) throw new Error('sfu_operations_filter_invalid');
  params.set(key, value);
}

function normalizeBase(value: string, allowInsecureLocalhost: boolean): string {
  const base = String(value || '').trim().replace(/\/+$/, '');
  if (!isAllowedSfuEndpoint(base, 'http', allowInsecureLocalhost)) {
    throw new Error('sfu_operations_hub_url_invalid');
  }
  return base;
}

function diagnosticRef(value: unknown, nullable: boolean): string | null {
  if (nullable && value === null) return null;
  if (typeof value !== 'string' || !identifier(value)) throw new Error('sfu_operations_response_invalid');
  return value;
}

function status(value: unknown, error: string): string {
  if (typeof value !== 'string' || !/^[a-z0-9][a-z0-9_.:-]{0,95}$/.test(value)) throw new Error(error);
  return value;
}

function reason(value: unknown): string {
  if (typeof value !== 'string' || !/^sfu_[a-z0-9_]{2,116}$/.test(value)) {
    throw new Error('sfu_command_response_invalid');
  }
  return value;
}

function identifier(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
}

function cursor(value: unknown): string {
  if (typeof value !== 'string' || value.length < 1 || value.length > 512 || /[\u0000-\u0020\u007f]/.test(value)) {
    throw new Error('sfu_operations_cursor_invalid');
  }
  return value;
}

function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('sfu_command_payload_invalid');
  return value;
}
