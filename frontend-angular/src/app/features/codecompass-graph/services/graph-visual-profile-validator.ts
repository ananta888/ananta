import {
  canonicalGraphVisualProfileJson,
  EDGE_VISUAL_METRIC_IDS,
  GRAPH_VISUAL_PROFILE_MAX_DEPTH,
  GRAPH_VISUAL_PROFILE_MAX_WEIGHT,
  GRAPH_VISUAL_PROFILE_SCHEMA_VERSION,
  GraphMetricDirection,
  GraphMetricNormalization,
  GraphVisualProfile,
  graphVisualProfileHash,
  NODE_VISUAL_METRIC_IDS,
  WeightedMetricConfig,
} from '../models/graph-visual-profile.model';

export interface GraphVisualProfileValidationIssue {
  path: string;
  reasonCode: string;
  message: string;
}

export interface GraphVisualProfileValidationResult {
  valid: boolean;
  issues: readonly GraphVisualProfileValidationIssue[];
  profile?: GraphVisualProfile;
  canonicalJson?: string;
  profileHash?: string;
}

const TOP_LEVEL_KEYS = new Set([
  'schemaVersion',
  'profileId',
  'name',
  'nodeMetrics',
  'edgeMetrics',
  'nodeSizeRange',
  'edgeThicknessRange',
  'highlightFactors',
  'domainColorOverrides',
  'nodeKindColorOverrides',
  'relationColorOverrides',
  'legend',
]);
const METRIC_KEYS = new Set(['metricId', 'enabled', 'weight', 'normalization', 'direction']);
const RANGE_KEYS = new Set(['min', 'max']);
const HIGHLIGHT_KEYS = new Set(['hover', 'selected', 'connected']);
const LEGEND_KEYS = new Set(['showDomains', 'showRelations', 'showMetrics', 'showUnavailable']);
const DANGEROUS_KEYS = new Set(['__proto__', 'prototype', 'constructor']);
const COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;
const PROFILE_ID_PATTERN = /^[a-zA-Z0-9](?:[a-zA-Z0-9._-]{0,63})$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function frozenClone<T>(value: T): T {
  if (value && typeof value === 'object') {
    for (const nested of Object.values(value as Record<string, unknown>)) frozenClone(nested);
    Object.freeze(value);
  }
  return value;
}

export class GraphVisualProfileValidator {
  validate(input: unknown): GraphVisualProfileValidationResult {
    const issues: GraphVisualProfileValidationIssue[] = [];
    this.validateObjectSafety(input, '$', 0, new Set<object>(), issues);
    if (!isRecord(input)) {
      this.issue(issues, '$', 'profile_not_object', 'Profile must be a plain object.');
      return { valid: false, issues };
    }

    this.rejectUnknownKeys(input, TOP_LEVEL_KEYS, '$', issues);
    if (input['schemaVersion'] !== GRAPH_VISUAL_PROFILE_SCHEMA_VERSION) {
      this.issue(issues, '$.schemaVersion', 'unsupported_schema_version', 'Only schema version 1 is supported.');
    }
    this.validateString(input['profileId'], '$.profileId', issues, 1, 64, PROFILE_ID_PATTERN, 'invalid_profile_id');
    this.validateString(input['name'], '$.name', issues, 1, 120);
    this.validateMetricConfigs(input['nodeMetrics'], '$.nodeMetrics', new Set(NODE_VISUAL_METRIC_IDS), issues);
    this.validateMetricConfigs(input['edgeMetrics'], '$.edgeMetrics', new Set(EDGE_VISUAL_METRIC_IDS), issues);
    this.validateRange(input['nodeSizeRange'], '$.nodeSizeRange', 1, 100, issues);
    this.validateRange(input['edgeThicknessRange'], '$.edgeThicknessRange', 0.1, 20, issues);
    this.validateHighlightFactors(input['highlightFactors'], issues);
    this.validateColorOverrides(input['domainColorOverrides'], '$.domainColorOverrides', issues);
    this.validateColorOverrides(input['nodeKindColorOverrides'], '$.nodeKindColorOverrides', issues);
    this.validateColorOverrides(input['relationColorOverrides'], '$.relationColorOverrides', issues);
    this.validateLegend(input['legend'], issues);

    if (issues.length > 0) return { valid: false, issues };

    const profile = this.toCanonicalProfile(input);
    const serialized = canonicalGraphVisualProfileJson(profile);
    const canonicalProfile = frozenClone(JSON.parse(serialized) as GraphVisualProfile);
    return {
      valid: true,
      issues: [],
      profile: canonicalProfile,
      canonicalJson: serialized,
      profileHash: graphVisualProfileHash(canonicalProfile),
    };
  }

  private validateObjectSafety(
    value: unknown,
    path: string,
    depth: number,
    ancestors: Set<object>,
    issues: GraphVisualProfileValidationIssue[],
  ): void {
    if (!value || typeof value !== 'object') return;
    if (depth > GRAPH_VISUAL_PROFILE_MAX_DEPTH) {
      this.issue(issues, path, 'max_depth_exceeded', `Object depth exceeds ${GRAPH_VISUAL_PROFILE_MAX_DEPTH}.`);
      return;
    }
    if (ancestors.has(value)) {
      this.issue(issues, path, 'cyclic_object', 'Cyclic profile data is not supported.');
      return;
    }
    if (!Array.isArray(value) && !isRecord(value)) {
      this.issue(issues, path, 'non_plain_object', 'Only arrays and plain objects are accepted.');
      return;
    }
    const nextAncestors = new Set(ancestors).add(value);
    for (const key of Object.keys(value)) {
      if (DANGEROUS_KEYS.has(key)) {
        this.issue(issues, `${path}.${key}`, 'dangerous_property', `Property ${key} is forbidden.`);
      }
      this.validateObjectSafety(
        (value as Record<string, unknown>)[key],
        Array.isArray(value) ? `${path}[${key}]` : `${path}.${key}`,
        depth + 1,
        nextAncestors,
        issues,
      );
    }
  }

  private validateMetricConfigs(
    value: unknown,
    path: string,
    allowedMetricIds: ReadonlySet<string>,
    issues: GraphVisualProfileValidationIssue[],
  ): void {
    if (!Array.isArray(value)) {
      this.issue(issues, path, 'metrics_not_array', 'Metric configuration must be an array.');
      return;
    }
    const seen = new Set<string>();
    value.forEach((entry, index) => {
      const entryPath = `${path}[${index}]`;
      if (!isRecord(entry)) {
        this.issue(issues, entryPath, 'metric_not_object', 'Metric configuration must be a plain object.');
        return;
      }
      this.rejectUnknownKeys(entry, METRIC_KEYS, entryPath, issues);
      const metricId = entry['metricId'];
      if (typeof metricId !== 'string' || !allowedMetricIds.has(metricId)) {
        this.issue(issues, `${entryPath}.metricId`, 'unknown_metric_id', 'Metric ID is not allowed for this entity.');
      } else if (seen.has(metricId)) {
        this.issue(issues, `${entryPath}.metricId`, 'duplicate_metric_id', 'Metric IDs must be unique.');
      } else {
        seen.add(metricId);
      }
      if (typeof entry['enabled'] !== 'boolean') {
        this.issue(issues, `${entryPath}.enabled`, 'invalid_boolean', 'Enabled must be a boolean.');
      }
      this.validateFiniteNumber(entry['weight'], `${entryPath}.weight`, issues, 0, GRAPH_VISUAL_PROFILE_MAX_WEIGHT);
      if (!(['linear', 'log1p', 'sqrt'] as GraphMetricNormalization[]).includes(entry['normalization'] as GraphMetricNormalization)) {
        this.issue(issues, `${entryPath}.normalization`, 'unknown_normalization', 'Normalization must be linear, log1p or sqrt.');
      }
      if (!(['normal', 'inverse'] as GraphMetricDirection[]).includes(entry['direction'] as GraphMetricDirection)) {
        this.issue(issues, `${entryPath}.direction`, 'unknown_direction', 'Direction must be normal or inverse.');
      }
    });
  }

  private validateRange(
    value: unknown,
    path: string,
    lowerBound: number,
    upperBound: number,
    issues: GraphVisualProfileValidationIssue[],
  ): void {
    if (!isRecord(value)) {
      this.issue(issues, path, 'range_not_object', 'Render range must be a plain object.');
      return;
    }
    this.rejectUnknownKeys(value, RANGE_KEYS, path, issues);
    this.validateFiniteNumber(value['min'], `${path}.min`, issues, lowerBound, upperBound);
    this.validateFiniteNumber(value['max'], `${path}.max`, issues, lowerBound, upperBound);
    if (typeof value['min'] === 'number' && typeof value['max'] === 'number' && value['min'] > value['max']) {
      this.issue(issues, path, 'range_min_greater_than_max', 'Range minimum must be less than or equal to maximum.');
    }
  }

  private validateHighlightFactors(value: unknown, issues: GraphVisualProfileValidationIssue[]): void {
    const path = '$.highlightFactors';
    if (!isRecord(value)) {
      this.issue(issues, path, 'highlight_not_object', 'Highlight factors must be a plain object.');
      return;
    }
    this.rejectUnknownKeys(value, HIGHLIGHT_KEYS, path, issues);
    for (const key of HIGHLIGHT_KEYS) {
      this.validateFiniteNumber(value[key], `${path}.${key}`, issues, 1, 5);
    }
  }

  private validateColorOverrides(
    value: unknown,
    path: string,
    issues: GraphVisualProfileValidationIssue[],
  ): void {
    if (!isRecord(value)) {
      this.issue(issues, path, 'color_overrides_not_object', 'Color overrides must be a plain object.');
      return;
    }
    for (const [key, color] of Object.entries(value)) {
      if (!key.trim() || key.length > 256) {
        this.issue(issues, `${path}.${key}`, 'invalid_override_key', 'Override keys must contain 1 to 256 characters.');
      }
      if (DANGEROUS_KEYS.has(key)) {
        this.issue(issues, `${path}.${key}`, 'dangerous_property', `Property ${key} is forbidden.`);
      }
      if (typeof color !== 'string' || !COLOR_PATTERN.test(color)) {
        this.issue(issues, `${path}.${key}`, 'invalid_color', 'Only safe #RRGGBB colors are accepted.');
      }
    }
  }

  private validateLegend(value: unknown, issues: GraphVisualProfileValidationIssue[]): void {
    const path = '$.legend';
    if (!isRecord(value)) {
      this.issue(issues, path, 'legend_not_object', 'Legend settings must be a plain object.');
      return;
    }
    this.rejectUnknownKeys(value, LEGEND_KEYS, path, issues);
    for (const key of LEGEND_KEYS) {
      if (typeof value[key] !== 'boolean') {
        this.issue(issues, `${path}.${key}`, 'invalid_boolean', 'Legend settings must be boolean.');
      }
    }
  }

  private validateFiniteNumber(
    value: unknown,
    path: string,
    issues: GraphVisualProfileValidationIssue[],
    min: number,
    max: number,
  ): void {
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      this.issue(issues, path, 'number_not_finite', 'Value must be a finite number.');
      return;
    }
    if (value < min || value > max) {
      this.issue(issues, path, 'number_out_of_range', `Value must be between ${min} and ${max}.`);
    }
  }

  private validateString(
    value: unknown,
    path: string,
    issues: GraphVisualProfileValidationIssue[],
    minLength: number,
    maxLength: number,
    pattern?: RegExp,
    patternReason = 'invalid_string',
  ): void {
    if (typeof value !== 'string' || value.length < minLength || value.length > maxLength) {
      this.issue(issues, path, 'invalid_string_length', `String length must be ${minLength} to ${maxLength}.`);
      return;
    }
    if (pattern && !pattern.test(value)) {
      this.issue(issues, path, patternReason, 'String format is invalid.');
    }
  }

  private rejectUnknownKeys(
    value: Record<string, unknown>,
    allowed: ReadonlySet<string>,
    path: string,
    issues: GraphVisualProfileValidationIssue[],
  ): void {
    for (const key of Object.keys(value)) {
      if (!allowed.has(key)) {
        this.issue(issues, `${path}.${key}`, 'unknown_property', `Unknown property ${key}.`);
      }
    }
  }

  private toCanonicalProfile(value: Record<string, unknown>): GraphVisualProfile {
    const canonicalColors = (input: unknown): Record<string, string> => Object.fromEntries(
      Object.entries(input as Record<string, string>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, color]) => [key, color.toUpperCase()]),
    );
    const canonicalMetrics = <T extends string>(input: unknown): WeightedMetricConfig<any>[] =>
      ([...(input as WeightedMetricConfig<any>[])]).sort((left, right) =>
        (left.metricId as T).localeCompare(right.metricId as T));

    return {
      schemaVersion: GRAPH_VISUAL_PROFILE_SCHEMA_VERSION,
      profileId: value['profileId'] as string,
      name: value['name'] as string,
      nodeMetrics: canonicalMetrics(value['nodeMetrics']),
      edgeMetrics: canonicalMetrics(value['edgeMetrics']),
      nodeSizeRange: { ...(value['nodeSizeRange'] as { min: number; max: number }) },
      edgeThicknessRange: { ...(value['edgeThicknessRange'] as { min: number; max: number }) },
      highlightFactors: { ...(value['highlightFactors'] as { hover: number; selected: number; connected: number }) },
      domainColorOverrides: canonicalColors(value['domainColorOverrides']),
      nodeKindColorOverrides: canonicalColors(value['nodeKindColorOverrides']),
      relationColorOverrides: canonicalColors(value['relationColorOverrides']),
      legend: { ...(value['legend'] as GraphVisualProfile['legend']) },
    };
  }

  private issue(
    issues: GraphVisualProfileValidationIssue[],
    path: string,
    reasonCode: string,
    message: string,
  ): void {
    if (!issues.some(issue => issue.path === path && issue.reasonCode === reasonCode)) {
      issues.push({ path, reasonCode, message });
    }
  }
}
