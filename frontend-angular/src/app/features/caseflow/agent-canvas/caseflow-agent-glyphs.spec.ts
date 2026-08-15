/**
 * An icon that is missing renders as a hole a person cannot read past.
 *
 * The canvas role catalog and this glyph table are separate lists, so the
 * first test is the one that matters: every icon the catalog can ask for has
 * a glyph, checked against the real catalog rather than a copy.
 */

import { describe, expect, it } from 'vitest';
import { CASEFLOW_AGENT_ROLE_CATALOG } from './caseflow-role-catalog';
import {
  agentGlyph,
  glyphForIconName,
  knownIconNames,
  levelGlyph,
  levelLabel,
  statusGlyph,
} from './caseflow-agent-glyphs';

describe('agent glyphs', () => {
  it('has a glyph for every icon the shipped role catalog asks for', () => {
    const asked = new Set(CASEFLOW_AGENT_ROLE_CATALOG.map(role => role.default_icon));

    expect([...asked].filter(icon => !knownIconNames().includes(icon))).toEqual([]);
  });

  it('gives visibly different roles visibly different glyphs', () => {
    expect(agentGlyph({ role: 'developer' })).not.toBe(agentGlyph({ role: 'tester' }));
    expect(agentGlyph({ role: 'security' })).not.toBe(agentGlyph({ role: 'designer' }));
  });

  it('prefers an explicit icon over the one the role would imply', () => {
    expect(agentGlyph({ icon: 'palette', role: 'developer' })).toBe(agentGlyph({ role: 'artist' }));
  });

  it('still marks an unknown role as an agent rather than as nothing', () => {
    for (const value of ['', '   ', 'not-a-role', null, undefined]) {
      expect(agentGlyph({ role: value as string | null })).toBeTruthy();
    }
    expect(agentGlyph()).toBeTruthy();
  });

  it('falls back to the role when the icon is one it does not know', () => {
    expect(agentGlyph({ icon: 'not_an_icon', role: 'tester' })).toBe(agentGlyph({ role: 'tester' }));
  });
});

describe('level glyphs', () => {
  it('names and marks every level the overview walks through', () => {
    for (const level of ['organization', 'unit', 'value_stream', 'team', 'role_slot', 'agent'] as const) {
      expect(levelGlyph(level)).toBeTruthy();
      expect(levelLabel(level)).toBeTruthy();
    }
  });

  it('keeps an organisation distinguishable from a team at a glance', () => {
    expect(levelGlyph('organization')).not.toBe(levelGlyph('team'));
    expect(levelGlyph('team')).not.toBe(levelGlyph('agent'));
  });
});

describe('status glyphs', () => {
  it('marks running apart from finished and from failed', () => {
    expect(statusGlyph('running')).not.toBe(statusGlyph('success'));
    expect(statusGlyph('success')).not.toBe(statusGlyph('error'));
  });

  it('never renders an unknown status as a blank', () => {
    for (const value of ['', 'nonsense', null, undefined]) {
      expect(statusGlyph(value)).toBeTruthy();
    }
  });
});

describe('drawing an icon name', () => {
  it('has a glyph for every runtime status the canvas can show', () => {
    for (const icon of ['schedule', 'play_circle', 'approval', 'check_circle', 'error', 'skip_next', 'cancel', 'help_outline']) {
      expect(knownIconNames()).toContain(icon);
    }
  });

  it('never draws the icon name itself, which is what a missing font did', () => {
    for (const icon of ['code', 'bug_report', 'not_an_icon', '', null]) {
      expect(glyphForIconName(icon)).not.toBe(icon);
      expect(glyphForIconName(icon)).toBeTruthy();
    }
  });
});
