import { DOCUMENT } from '@angular/common';
import { Injectable, OnDestroy, inject } from '@angular/core';

import {
  BatteryBucket,
  CapabilityAdvertisement,
  CapabilityConsent,
  CapabilityRuntimeOutcome,
  CapabilitySignature,
  CodecBucket,
  CpuBucket,
  GpuBucket,
  MemoryBucket,
  NetworkBucket,
  PEER_CAPABILITY_TTL_MS,
  PeerCapabilityLimits,
  PeerResourceProfile,
} from './peer-capability.types';

export interface CapabilitySigner {
  sign(payload: Readonly<Record<string, unknown>>): Promise<CapabilitySignature>;
}

export interface CapabilityClock {
  now(): number;
}

const CPU_ORDER: CpuBucket[] = ['unknown', 'low', 'medium', 'high'];
const MEMORY_ORDER: MemoryBucket[] = ['unknown', 'low', 'medium', 'high'];
const CAPABILITY_FIELDS = [
  'schema', 'advertisement_id', 'session_id', 'epoch', 'sender_id', 'algorithms',
  'roles', 'task_types', 'resource_profile', 'measurements_expires_at_ms',
  'expires_at_ms', 'max_delay_ms', 'max_artifact_bytes', 'signature',
] as const;
const CAPABILITY_ID = /^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/;

@Injectable({ providedIn: 'root' })
export class PeerCapabilityService implements OnDestroy {
  private readonly document = inject(DOCUMENT);
  private abort?: AbortController;
  private activeSessionId = '';
  private observedCpu: CpuBucket = 'high';

  private readonly visibilityListener = (): void => {
    if (this.document.visibilityState !== 'visible') this.stop('visibility_lost');
  };

  constructor() {
    this.document.addEventListener('visibilitychange', this.visibilityListener);
  }

  async measureAndAdvertise(options: {
    consent: CapabilityConsent;
    sessionId: string;
    roomId?: string;
    epoch: number;
    senderId: string;
    limits: PeerCapabilityLimits;
    selfClaim?: Partial<PeerResourceProfile>;
    algorithms: string[];
    roles: CapabilityAdvertisement['roles'];
    taskTypes: CapabilityAdvertisement['task_types'];
    signer: CapabilitySigner;
    clock?: CapabilityClock;
  }): Promise<CapabilityAdvertisement> {
    if (!options.consent.granted || options.consent.version < 1) throw new Error('compute_consent_required');
    if (this.document.visibilityState !== 'visible') throw new Error('document_not_visible');
    if (!options.sessionId || options.epoch < 1) throw new Error('session_scope_invalid');
    this.stop('replaced');
    const controller = new AbortController();
    this.abort = controller;
    this.activeSessionId = options.sessionId;
    const clock = options.clock ?? { now: () => Date.now() };
    const measured = await this.measure(controller.signal);
    if (controller.signal.aborted || this.activeSessionId !== options.sessionId) throw new Error('measurement_cancelled');
    const effective = this.reduce(measured, options.selfClaim ?? {}, options.limits);
    const now = clock.now();
    const unsigned: Omit<CapabilityAdvertisement, 'signature'> = {
      schema: 'ananta.semantic-capability-advertisement.v1',
      advertisement_id: `cap-${options.senderId}-${options.epoch}-${now}`,
      session_id: options.sessionId,
      ...(options.roomId ? { room_id: options.roomId } : {}),
      epoch: options.epoch,
      sender_id: options.senderId,
      algorithms: [...new Set(options.algorithms)].sort(),
      roles: [...new Set(options.roles)].sort(),
      task_types: [...new Set(options.taskTypes)].sort(),
      resource_profile: effective,
      measurements_expires_at_ms: now + PEER_CAPABILITY_TTL_MS,
      expires_at_ms: now + PEER_CAPABILITY_TTL_MS,
      max_delay_ms: Math.max(2_000, Math.min(20_000, Math.floor(options.limits.maxDelayMs / 1000) * 1000)),
      max_artifact_bytes: Math.max(1_024, Math.min(4_194_304, Math.floor(options.limits.maxArtifactBytes / 1024) * 1024)),
    };
    const signature = await options.signer.sign(unsigned as unknown as Record<string, unknown>);
    return parseCapabilityAdvertisement({ ...unsigned, signature }, now);
  }

  recordRuntimeOutcome(outcome: CapabilityRuntimeOutcome): void {
    if (!outcome.successful) this.observedCpu = lower(outcome.cpu, 'low', CPU_ORDER);
    else this.observedCpu = lower(this.observedCpu, outcome.cpu, CPU_ORDER);
  }

  endSession(sessionId: string): void {
    if (this.activeSessionId === sessionId) this.stop('session_ended');
  }

  revokeConsent(): void {
    this.stop('consent_revoked');
  }

  stop(_reason: string): void {
    this.abort?.abort();
    this.abort = undefined;
    this.activeSessionId = '';
  }

  ngOnDestroy(): void {
    this.stop('destroyed');
    this.document.removeEventListener('visibilitychange', this.visibilityListener);
  }

  private async measure(signal: AbortSignal): Promise<PeerResourceProfile> {
    const nav = navigator as Navigator & Record<string, unknown>;
    const cpu = lower(
      this.cpuBucket(Number(nav['hardwareConcurrency'] ?? 0)),
      this.benchmarkCpu(signal),
      CPU_ORDER,
    );
    const memory = this.memoryBucket(Number(nav['deviceMemory'] ?? 0));
    const gpu: GpuBucket = nav['gpu'] ? 'integrated' : 'unknown';
    const codec: CodecBucket = 'VideoEncoder' in globalThis ? 'hardware' : 'unknown';
    const battery = await this.batteryBucket(nav, signal);
    const connection = nav['connection'] as { effectiveType?: unknown; saveData?: unknown } | undefined;
    const network = this.networkBucket(connection);
    return { cpu, memory, gpu, codec, battery, network };
  }

  private reduce(
    measured: PeerResourceProfile,
    selfClaim: Partial<PeerResourceProfile>,
    limits: PeerCapabilityLimits,
  ): PeerResourceProfile {
    return {
      cpu: lower(lower(measured.cpu, selfClaim.cpu ?? measured.cpu, CPU_ORDER), limits.cpu, CPU_ORDER, this.observedCpu),
      memory: lower(measured.memory, selfClaim.memory ?? measured.memory, MEMORY_ORDER, limits.memory),
      gpu: measured.gpu,
      codec: measured.codec,
      battery: measured.battery,
      network: measured.network,
    };
  }

  private cpuBucket(cores: number): CpuBucket {
    if (!Number.isFinite(cores) || cores < 1) return 'unknown';
    if (cores <= 2) return 'low';
    if (cores <= 8) return 'medium';
    return 'high';
  }

  private benchmarkCpu(signal: AbortSignal): CpuBucket {
    const started = performance.now();
    let accumulator = 0;
    for (let index = 0; index < 50_000; index += 1) accumulator = (accumulator + index * 17) % 65_521;
    if (signal.aborted || accumulator < 0) return 'unknown';
    const elapsed = performance.now() - started;
    if (!Number.isFinite(elapsed)) return 'unknown';
    if (elapsed > 20) return 'low';
    if (elapsed > 5) return 'medium';
    return 'high';
  }

  private memoryBucket(gib: number): MemoryBucket {
    if (!Number.isFinite(gib) || gib <= 0) return 'unknown';
    if (gib <= 2) return 'low';
    if (gib <= 8) return 'medium';
    return 'high';
  }

  private async batteryBucket(nav: Navigator & Record<string, unknown>, signal: AbortSignal): Promise<BatteryBucket> {
    const getter = nav['getBattery'];
    if (typeof getter !== 'function') return 'unknown';
    try {
      const battery = await (getter as () => Promise<{ charging?: boolean; level?: number }>).call(nav);
      if (signal.aborted) return 'unknown';
      if (battery.charging) return 'mains';
      if (typeof battery.level !== 'number') return 'unknown';
      return battery.level <= 0.2 ? 'critical' : 'limited';
    } catch {
      return 'unknown';
    }
  }

  private networkBucket(connection?: { effectiveType?: unknown; saveData?: unknown }): NetworkBucket {
    if (!connection) return 'unknown';
    if (connection.saveData === true || connection.effectiveType === 'slow-2g' || connection.effectiveType === '2g') return 'constrained';
    if (connection.effectiveType === '4g') return 'fast';
    return connection.effectiveType === '3g' ? 'normal' : 'unknown';
  }
}

/** Parse an untrusted cross-peer advertisement before it enters scheduling. */
export function parseCapabilityAdvertisement(raw: unknown, nowMs?: number): CapabilityAdvertisement {
  const value = capabilityRecord(raw, CAPABILITY_FIELDS, ['room_id']);
  if (value['schema'] !== 'ananta.semantic-capability-advertisement.v1') throw new Error('invalid_schema');
  for (const field of ['advertisement_id', 'session_id', 'sender_id']) capabilityIdentifier(value[field]);
  if (value['room_id'] != null) capabilityIdentifier(value['room_id']);
  const epoch = capabilityInteger(value['epoch'], 1, 2_147_483_647);
  const algorithms = capabilityStrings(value['algorithms'], 16);
  if (algorithms.some(item => !/^[a-z0-9][a-z0-9_.-]{0,63}$/.test(item) || ![
    'heuristic-visual-v1', 'speech-features-v1', 'semantic-validator-v1', 'ordinary-fallback-v1',
  ].includes(item))) throw new Error('unknown_capability');
  const roles = capabilityStrings(value['roles'], 4);
  if (roles.some(item => !['executor', 'validator', 'standby'].includes(item))) throw new Error('invalid_capability');
  const taskTypes = capabilityStrings(value['task_types'], 8);
  if (taskTypes.some(item => ![
    'visual_extract', 'visual_validate', 'speech_features', 'speech_validate',
  ].includes(item))) throw new Error('invalid_capability');
  const profile = capabilityRecord(value['resource_profile'], [
    'cpu', 'memory', 'gpu', 'codec', 'battery', 'network',
  ], []);
  const allowedProfile: Record<string, readonly string[]> = {
    cpu: ['unknown', 'low', 'medium', 'high'], memory: ['unknown', 'low', 'medium', 'high'],
    gpu: ['unknown', 'none', 'integrated', 'dedicated'], codec: ['unknown', 'software', 'hardware'],
    battery: ['unknown', 'critical', 'limited', 'mains'], network: ['unknown', 'constrained', 'normal', 'fast'],
  };
  if (Object.entries(allowedProfile).some(([field, allowed]) => !allowed.includes(String(profile[field])))) {
    throw new Error('invalid_resource_profile');
  }
  const measurementsExpiry = capabilityInteger(value['measurements_expires_at_ms'], 1, Number.MAX_SAFE_INTEGER);
  const expiry = capabilityInteger(value['expires_at_ms'], 1, Number.MAX_SAFE_INTEGER);
  if (expiry > measurementsExpiry) throw new Error('stale_measurement');
  if (nowMs !== undefined && expiry <= nowMs) throw new Error('capability_expired');
  const signature = capabilityRecord(value['signature'], ['algorithm', 'key_id', 'value'], []);
  if (!['ed25519', 'hmac-sha256'].includes(String(signature['algorithm']))) throw new Error('invalid_signature');
  capabilityIdentifier(signature['key_id']);
  if (typeof signature['value'] !== 'string' || signature['value'].length < 16 || signature['value'].length > 512) {
    throw new Error('invalid_signature');
  }
  return Object.freeze({
    schema: 'ananta.semantic-capability-advertisement.v1',
    advertisement_id: String(value['advertisement_id']), session_id: String(value['session_id']),
    ...(value['room_id'] == null ? {} : { room_id: String(value['room_id']) }),
    epoch, sender_id: String(value['sender_id']), algorithms,
    roles: roles as CapabilityAdvertisement['roles'], task_types: taskTypes as CapabilityAdvertisement['task_types'],
    resource_profile: Object.freeze({ ...profile }) as unknown as PeerResourceProfile,
    measurements_expires_at_ms: measurementsExpiry, expires_at_ms: expiry,
    max_delay_ms: capabilityInteger(value['max_delay_ms'], 2_000, 20_000),
    max_artifact_bytes: capabilityInteger(value['max_artifact_bytes'], 1_024, 4_194_304),
    signature: Object.freeze({
      algorithm: signature['algorithm'], key_id: signature['key_id'], value: signature['value'],
    }) as CapabilitySignature,
  });
}

function capabilityRecord(
  raw: unknown,
  required: readonly string[],
  optional: readonly string[],
): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('invalid_capability');
  const value = raw as Record<string, unknown>;
  const allowed = new Set([...required, ...optional]);
  if (Object.keys(value).some(key => !allowed.has(key)) || required.some(key => !(key in value))) {
    throw new Error('invalid_capability');
  }
  return value;
}

function capabilityIdentifier(value: unknown): string {
  if (typeof value !== 'string' || !CAPABILITY_ID.test(value)) throw new Error('invalid_identifier');
  return value;
}

function capabilityInteger(value: unknown, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || Number(value) < minimum || Number(value) > maximum) {
    throw new Error('impossible_budget');
  }
  return Number(value);
}

function capabilityStrings(value: unknown, maximum: number): string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > maximum
      || value.some(item => typeof item !== 'string') || new Set(value).size !== value.length) {
    throw new Error('invalid_capability');
  }
  return [...value].sort() as string[];
}

function lower<T extends string>(first: T, second: T, order: readonly T[], ...rest: T[]): T {
  return [first, second, ...rest].reduce((lowest, value) => {
    const index = order.indexOf(value);
    const lowestIndex = order.indexOf(lowest);
    if (index < 0) return order[0];
    return index < lowestIndex ? value : lowest;
  });
}
