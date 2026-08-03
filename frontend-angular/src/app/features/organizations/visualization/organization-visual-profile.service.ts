import { Injectable, signal } from '@angular/core';

import { OrganizationEdgeKind, OrganizationEdgeNamespace, OrganizationNodeKind } from '../models/organization-topology.models';
import {
  DEFAULT_ORGANIZATION_VISUAL_PROFILE,
  OrganizationEdgeColorMetric,
  OrganizationEdgeStrengthMetric,
  OrganizationLeadershipScope,
  OrganizationNodeColorMetric,
  OrganizationNodeSizeMetric,
  OrganizationRoleVisualOverride,
  OrganizationVisualProfile,
} from './organization-visual-profile.models';

const SAFE_COLOR = /^#[0-9A-F]{6}$/i;
const UNSAFE_OVERRIDE_KEYS = new Set(['__proto__', 'prototype', 'constructor']);
const MAX_ROLE_OVERRIDES = 500;
const NODE_SIZE_METRICS = new Set<OrganizationNodeSizeMetric>(['kind', 'importance', 'leadership_scope', 'runtime_load', 'degree']);
const NODE_COLOR_METRICS = new Set<OrganizationNodeColorMetric>(['kind', 'importance', 'leadership_scope', 'runtime_state']);
const EDGE_COLOR_METRICS = new Set<OrganizationEdgeColorMetric>(['namespace', 'kind']);
const EDGE_STRENGTH_METRICS = new Set<OrganizationEdgeStrengthMetric>(['fixed', 'kind_weight', 'runtime_provenance']);
const LEADERSHIP_SCOPES = new Set<OrganizationLeadershipScope>(['none', 'team', 'multi_team', 'organization']);

@Injectable()
export class OrganizationVisualProfileService {
  readonly profile = signal<Readonly<OrganizationVisualProfile>>(DEFAULT_ORGANIZATION_VISUAL_PROFILE);

  roleOverride(key: string): Readonly<OrganizationRoleVisualOverride> | undefined {
    const normalizedKey = String(key || '').trim();
    if (!isSafeOverrideKey(normalizedKey)) return undefined;
    return ownOverride(this.profile().roleOverrides, normalizedKey);
  }

  setNodeSizeMetric(value: OrganizationNodeSizeMetric): void {
    if (!NODE_SIZE_METRICS.has(value)) return;
    this.patch({ nodeSizeMetric: value });
  }

  setNodeColorMetric(value: OrganizationNodeColorMetric): void {
    if (!NODE_COLOR_METRICS.has(value)) return;
    this.patch({ nodeColorMetric: value });
  }

  setEdgeColorMetric(value: OrganizationEdgeColorMetric): void {
    if (!EDGE_COLOR_METRICS.has(value)) return;
    this.patch({ edgeColorMetric: value });
  }

  setEdgeStrengthMetric(value: OrganizationEdgeStrengthMetric): void {
    if (!EDGE_STRENGTH_METRICS.has(value)) return;
    this.patch({ edgeStrengthMetric: value });
  }

  setNodeRange(bound: 'min' | 'max', value: number): void {
    const current = this.profile();
    const bounded = boundedNumber(value, 1, 100);
    if (bounded === null) return;
    const next = { ...current.nodeSizeRange, [bound]: bounded };
    if (next.min > next.max) return;
    this.patch({ nodeSizeRange: next });
  }

  setEdgeRange(bound: 'min' | 'max', value: number): void {
    const current = this.profile();
    const bounded = boundedNumber(value, 0.1, 20);
    if (bounded === null) return;
    const next = { ...current.edgeWidthRange, [bound]: bounded };
    if (next.min > next.max) return;
    this.patch({ edgeWidthRange: next });
  }

  setNodeKindColor(kind: OrganizationNodeKind, color: string): void {
    const normalized = normalizeColor(color);
    if (!normalized) return;
    this.patch({
      nodeKindColors: { ...this.profile().nodeKindColors, [kind]: normalized },
    });
  }

  setEdgeNamespaceColor(namespace: OrganizationEdgeNamespace, color: string): void {
    const normalized = normalizeColor(color);
    if (!normalized) return;
    this.patch({
      edgeNamespaceColors: { ...this.profile().edgeNamespaceColors, [namespace]: normalized },
    });
  }

  setEdgeKindColor(kind: OrganizationEdgeKind, color: string): void {
    const normalized = normalizeColor(color);
    if (!normalized) return;
    this.patch({
      edgeKindColors: { ...this.profile().edgeKindColors, [kind]: normalized },
    });
  }

  setEdgeKindWeight(kind: OrganizationEdgeKind, value: number): void {
    const bounded = boundedNumber(value, 0, 1);
    if (bounded === null) return;
    this.patch({
      edgeKindWeights: {
        ...this.profile().edgeKindWeights,
        [kind]: bounded,
      },
    });
  }

  setRoleOverride(
    key: string,
    patch: Partial<OrganizationRoleVisualOverride>,
  ): void {
    const normalizedKey = String(key || '').trim();
    if (!isSafeOverrideKey(normalizedKey)) return;
    const roleOverrides = this.profile().roleOverrides;
    if (!ownOverride(roleOverrides, normalizedKey) && Object.keys(roleOverrides).length >= MAX_ROLE_OVERRIDES) return;
    if (patch.leadershipScope !== undefined && !LEADERSHIP_SCOPES.has(patch.leadershipScope)) return;
    const importance = boundedNumber(patch.importance ?? 50, 0, 100);
    if (importance === null) return;
    if (patch.color !== undefined && !normalizeColor(patch.color)) return;
    const current = this.roleOverride(normalizedKey) ?? {
      importance: 50,
      leadershipScope: 'none' as OrganizationLeadershipScope,
    };
    const color = patch.color === undefined ? current.color : normalizeColor(patch.color);
    const next: OrganizationRoleVisualOverride = {
      importance: patch.importance === undefined ? current.importance : importance,
      leadershipScope: patch.leadershipScope ?? current.leadershipScope,
      ...(color ? { color } : {}),
    };
    const nextOverrides = Object.assign(Object.create(null), this.profile().roleOverrides);
    nextOverrides[normalizedKey] = next;
    this.patch({ roleOverrides: Object.freeze(nextOverrides) });
  }

  clearRoleOverride(key: string): void {
    const normalizedKey = String(key || '').trim();
    if (!isSafeOverrideKey(normalizedKey)) return;
    const roleOverrides = Object.assign(Object.create(null), this.profile().roleOverrides);
    delete roleOverrides[normalizedKey];
    this.patch({ roleOverrides: Object.freeze(roleOverrides) });
  }

  reset(): void {
    this.profile.set(DEFAULT_ORGANIZATION_VISUAL_PROFILE);
  }

  private patch(patch: Partial<OrganizationVisualProfile>): void {
    this.profile.set(Object.freeze({ ...this.profile(), ...patch }));
  }
}

function boundedNumber(value: number, minimum: number, maximum: number): number | null {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.min(maximum, Math.max(minimum, numeric));
}

function normalizeColor(value: string): string | null {
  const color = String(value || '').trim().toUpperCase();
  return SAFE_COLOR.test(color) ? color : null;
}

function isSafeOverrideKey(key: string): boolean {
  return Boolean(key)
    && key.length <= 255
    && !UNSAFE_OVERRIDE_KEYS.has(key.toLocaleLowerCase());
}

function ownOverride(
  overrides: Readonly<Record<string, Readonly<OrganizationRoleVisualOverride>>>,
  key: string,
): Readonly<OrganizationRoleVisualOverride> | undefined {
  return Object.prototype.hasOwnProperty.call(overrides, key) ? overrides[key] : undefined;
}
