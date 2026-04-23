import { recommendBlueprint } from './blueprint-recommendation';

describe('recommendBlueprint', () => {
  it('maps security goals to Security-Review', () => {
    const recommendation = recommendBlueprint({
      goalType: 'security_review',
      strictness: 'strict',
      domain: 'security',
      executionStyle: 'iterative',
    });

    expect(recommendation.blueprintName).toBe('Security-Review');
    expect(recommendation.reasons.length).toBeGreaterThan(0);
  });

  it('maps research + evolution style to Research-Evolution', () => {
    const recommendation = recommendBlueprint({
      goalType: 'research',
      strictness: 'balanced',
      domain: 'general',
      executionStyle: 'evolution',
    });

    expect(recommendation.blueprintName).toBe('Research-Evolution');
  });

  it('maps bugfix + opencode to Scrum-OpenCode', () => {
    const recommendation = recommendBlueprint({
      goalType: 'bugfix',
      strictness: 'balanced',
      domain: 'software',
      executionStyle: 'opencode',
    });

    expect(recommendation.blueprintName).toBe('Scrum-OpenCode');
  });

  it('falls back to Scrum for generic iterative setup', () => {
    const recommendation = recommendBlueprint({
      goalType: 'project_setup',
      strictness: 'balanced',
      domain: 'general',
      executionStyle: 'iterative',
    });

    expect(recommendation.blueprintName).toBe('Scrum');
  });
});
