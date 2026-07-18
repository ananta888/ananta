import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { GraphVisualProfileValidator } from './graph-visual-profile-validator';

describe('documented graph visual profile', () => {
  it('is extracted from the viewer documentation and accepted by the production validator', () => {
    const documentation = readFileSync(
      resolve(process.cwd(), '../docs/codecompass-graph-viewer.md'),
      'utf-8',
    );
    const match = documentation.match(
      /<!-- graph-visual-profile-example:start -->\s*```json\s*([\s\S]*?)\s*```\s*<!-- graph-visual-profile-example:end -->/,
    );

    expect(match, 'documented profile block').not.toBeNull();
    const profile = JSON.parse(match![1]);
    const result = new GraphVisualProfileValidator().validate(profile);

    expect(result.issues).toEqual([]);
    expect(result.valid).toBe(true);
    expect(result.profileHash).toMatch(/^gvp1-[0-9a-f]{16}$/);
  });
});
