import {
  DEFAULT_GRAPH_VISUAL_PROFILE,
  GRAPH_VISUAL_PROFILE_MAX_DEPTH,
  GRAPH_VISUAL_PROFILE_MAX_WEIGHT,
  GRAPH_VISUAL_PROFILE_PRESETS,
} from '../models/graph-visual-profile.model';
import { GraphVisualProfileValidator } from './graph-visual-profile-validator';

function mutableProfile(): any {
  return JSON.parse(JSON.stringify(DEFAULT_GRAPH_VISUAL_PROFILE));
}

describe('GraphVisualProfileValidator', () => {
  const validator = new GraphVisualProfileValidator();

  it('validates every immutable built-in preset without browser state', () => {
    for (const profile of Object.values(GRAPH_VISUAL_PROFILE_PRESETS)) {
      const result = validator.validate(profile);
      expect(result.valid, profile.profileId).toBe(true);
      expect(result.profileHash).toMatch(/^gvp1-[a-f0-9]{16}$/);
      expect(Object.isFrozen(profile)).toBe(true);
      expect(Object.isFrozen(result.profile)).toBe(true);
    }
  });

  it.each([
    ['linear', 'normal'],
    ['log1p', 'normal'],
    ['sqrt', 'normal'],
    ['linear', 'inverse'],
    ['log1p', 'inverse'],
    ['sqrt', 'inverse'],
  ])('accepts normalization %s and direction %s', (normalization, direction) => {
    const profile = mutableProfile();
    profile.nodeMetrics[0].normalization = normalization;
    profile.nodeMetrics[0].direction = direction;
    expect(validator.validate(profile).valid).toBe(true);
  });

  it.each([
    ['negative weight', (profile: any) => profile.nodeMetrics[0].weight = -1, 'number_out_of_range'],
    ['excessive weight', (profile: any) => profile.nodeMetrics[0].weight = GRAPH_VISUAL_PROFILE_MAX_WEIGHT + 1, 'number_out_of_range'],
    ['NaN weight', (profile: any) => profile.nodeMetrics[0].weight = Number.NaN, 'number_not_finite'],
    ['infinite range', (profile: any) => profile.nodeSizeRange.max = Number.POSITIVE_INFINITY, 'number_not_finite'],
    ['reversed range', (profile: any) => profile.nodeSizeRange = { min: 20, max: 10 }, 'range_min_greater_than_max'],
    ['invalid highlight', (profile: any) => profile.highlightFactors.hover = 9, 'number_out_of_range'],
    ['unknown normalization', (profile: any) => profile.nodeMetrics[0].normalization = 'zscore', 'unknown_normalization'],
    ['unknown direction', (profile: any) => profile.nodeMetrics[0].direction = 'sideways', 'unknown_direction'],
    ['unknown metric', (profile: any) => profile.nodeMetrics[0].metricId = 'invented', 'unknown_metric_id'],
    ['duplicate metric', (profile: any) => profile.nodeMetrics[1].metricId = profile.nodeMetrics[0].metricId, 'duplicate_metric_id'],
  ])('rejects %s with a stable reason', (_label, mutate, reasonCode) => {
    const profile = mutableProfile();
    mutate(profile);
    const result = validator.validate(profile);
    expect(result.valid).toBe(false);
    expect(result.issues.some(issue => issue.reasonCode === reasonCode)).toBe(true);
  });

  it('accepts a zero weight sum as a defined fallback configuration', () => {
    const profile = mutableProfile();
    for (const metric of [...profile.nodeMetrics, ...profile.edgeMetrics]) {
      metric.enabled = true;
      metric.weight = 0;
    }
    expect(validator.validate(profile).valid).toBe(true);
  });

  it('rejects unsupported versions without mutating the input or default', () => {
    const profile = mutableProfile();
    profile.schemaVersion = 99;
    const snapshot = JSON.stringify(profile);

    const result = validator.validate(profile);

    expect(result.valid).toBe(false);
    expect(result.issues).toContainEqual(expect.objectContaining({ reasonCode: 'unsupported_schema_version' }));
    expect(JSON.stringify(profile)).toBe(snapshot);
    expect(DEFAULT_GRAPH_VISUAL_PROFILE.schemaVersion).toBe(1);
  });

  it('rejects dangerous, unknown and executable-looking properties', () => {
    const dangerous = JSON.parse(JSON.stringify(mutableProfile()).replace(
      /"domainColorOverrides":\{\}/,
      '"domainColorOverrides":{"__proto__":"#112233","safe":"url(javascript:alert(1))"}',
    ));
    dangerous.unexpected = true;

    const result = validator.validate(dangerous);

    expect(result.valid).toBe(false);
    expect(result.issues.map(issue => issue.reasonCode)).toEqual(expect.arrayContaining([
      'dangerous_property',
      'invalid_color',
      'unknown_property',
    ]));
    expect(({} as any).polluted).toBeUndefined();
  });

  it.each(['__proto__', 'constructor', 'prototype'])(
    'rejects a dangerous %s key even when deeply nested',
    dangerousKey => {
      const profile = mutableProfile();
      profile.domainColorOverrides = JSON.parse(
        `{"safe":{"nested":{"${dangerousKey}":{"polluted":true}}}}`,
      );

      const result = validator.validate(profile);

      expect(result.valid).toBe(false);
      expect(result.issues).toContainEqual(expect.objectContaining({
        path: `$.domainColorOverrides.safe.nested.${dangerousKey}`,
        reasonCode: 'dangerous_property',
      }));
      expect(({} as any).polluted).toBeUndefined();
    },
  );

  it('rejects cyclic and over-depth nested inputs without traversing forever', () => {
    const cyclic = mutableProfile();
    cyclic.domainColorOverrides = {};
    cyclic.domainColorOverrides.self = cyclic.domainColorOverrides;
    const cyclicResult = validator.validate(cyclic);
    expect(cyclicResult.valid).toBe(false);
    expect(cyclicResult.issues).toContainEqual(expect.objectContaining({
      reasonCode: 'cyclic_object',
    }));

    const tooDeep = mutableProfile();
    let nested: any = { leaf: '#112233' };
    for (let depth = 0; depth < GRAPH_VISUAL_PROFILE_MAX_DEPTH + 2; depth += 1) {
      nested = { next: nested };
    }
    tooDeep.domainColorOverrides = { nested };
    const depthResult = validator.validate(tooDeep);
    expect(depthResult.valid).toBe(false);
    expect(depthResult.issues).toContainEqual(expect.objectContaining({
      reasonCode: 'max_depth_exceeded',
    }));
  });

  it.each([
    'url(javascript:alert(1))',
    'var(--attacker-controlled)',
    'expression(alert(1))',
    'red',
    '#11223344',
    '#11223G',
    '#112233;',
    ' #112233',
  ])('rejects non-hex or executable-looking color %s', color => {
    const profile = mutableProfile();
    profile.relationColorOverrides = { calls: color };

    const result = validator.validate(profile);

    expect(result.valid).toBe(false);
    expect(result.issues).toContainEqual(expect.objectContaining({
      path: '$.relationColorOverrides.calls',
      reasonCode: 'invalid_color',
    }));
  });

  it('canonicalizes property order, metric order and color case for stable hashes', () => {
    const first = mutableProfile();
    first.domainColorOverrides = { zeta: '#aabbcc', alpha: '#112233' };
    const second = mutableProfile();
    second.profileId = 'same-visuals-different-id';
    second.name = 'Andere Bezeichnung';
    second.domainColorOverrides = { alpha: '#112233', zeta: '#AABBCC' };
    second.nodeMetrics.reverse();
    second.edgeMetrics.reverse();

    const left = validator.validate(first);
    const right = validator.validate(second);

    expect(left.valid).toBe(true);
    expect(right.valid).toBe(true);
    expect(left.profileHash).toBe(right.profileHash);
    expect(left.profile?.domainColorOverrides['zeta']).toBe('#AABBCC');
  });
});
