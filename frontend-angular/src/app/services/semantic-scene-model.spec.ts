import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  parseSemanticScene,
  SemanticSceneValidationError,
} from './semantic-scene-model';

function fixture(): Record<string, any> {
  const path = resolve(process.cwd(), '../tests/fixtures/webrtc/semantic_scene.v1.json');
  return JSON.parse(readFileSync(path, 'utf8'));
}

describe('semantic scene model', () => {
  it('roundtrips and deeply freezes the shared Python golden fixture', () => {
    const scene = parseSemanticScene(fixture());
    expect(scene.scene_id).toBe('scene-1');
    expect(Object.isFrozen(scene)).toBe(true);
    expect(Object.isFrozen(scene.nodes[0].geometry.value)).toBe(true);
  });

  it.each([
    ['geometry_out_of_bounds', (value: any) => Object.assign(value.nodes[0].geometry.value, { x: 0.9, width: 0.2 })],
    ['unknown_security_field', (value: any) => Object.assign(value.security, { encryption: 'invented' })],
    ['model_not_authoritative', (value: any) => Object.assign(value.nodes[0].label.provenance, { authoritative: true })],
  ])('rejects %s', (reason, mutate) => {
    const value = fixture();
    mutate(value);
    expect(() => parseSemanticScene(value)).toThrowError(
      expect.objectContaining<Partial<SemanticSceneValidationError>>({ reasonCode: reason }),
    );
  });

  it('rejects cycles and non-finite geometry', () => {
    const cyclic = fixture();
    cyclic.nodes[0].parent_id = cyclic.nodes[0].id;
    expect(() => parseSemanticScene(cyclic)).toThrowError(
      expect.objectContaining({ reasonCode: 'scene_cycle' }),
    );
    const nonFinite = fixture();
    nonFinite.nodes[0].geometry.value.x = Number.NaN;
    expect(() => parseSemanticScene(nonFinite)).toThrowError(
      expect.objectContaining({ reasonCode: 'non_finite' }),
    );
  });
});
