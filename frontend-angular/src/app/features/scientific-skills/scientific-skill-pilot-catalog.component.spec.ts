import { TestBed } from '@angular/core/testing';

import {
  ScientificSkillPilotCard,
  ScientificSkillPilotCatalogComponent,
} from './scientific-skill-pilot-catalog.component';

const card: ScientificSkillPilotCard = {
  entry_id: `skillentry_${'a'.repeat(64)}`,
  skill_name: 'astropy',
  upstream_pin: 'cc37669ed0f354619b1ae586e958609a87680718',
  allowed_mode: 'documentation-only',
  context_budget_tokens: 3620,
  allowed_tools: [],
  network_profile: 'denied',
  approval_level: 'none',
  approval_status: 'approved',
  source_reference: 'https://github.com/K-Dense-AI/scientific-agent-skills/blob/pin/skills/astropy/SKILL.md',
};

describe('ScientificSkillPilotCatalogComponent', () => {
  it('is invisible unless both feature flag and permission are granted', async () => {
    await TestBed.configureTestingModule({ imports: [ScientificSkillPilotCatalogComponent] }).compileComponents();
    const fixture = TestBed.createComponent(ScientificSkillPilotCatalogComponent);
    fixture.componentRef.setInput('cards', [card]);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.pilot')).toBeNull();

    fixture.componentRef.setInput('featureEnabled', true);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.pilot')).toBeNull();
  });

  it('shows pin, capability, context cost, approval and source before selection', async () => {
    await TestBed.configureTestingModule({ imports: [ScientificSkillPilotCatalogComponent] }).compileComponents();
    const fixture = TestBed.createComponent(ScientificSkillPilotCatalogComponent);
    fixture.componentRef.setInput('cards', [card]);
    fixture.componentRef.setInput('featureEnabled', true);
    fixture.componentRef.setInput('permissionGranted', true);
    fixture.detectChanges();

    const text = fixture.nativeElement.textContent;
    expect(text).toContain('astropy');
    expect(text).toContain(card.upstream_pin);
    expect(text).toContain('documentation-only');
    expect(text).toContain('3620 Tokens');
    expect(text).toContain('approved / none');
    const source = fixture.nativeElement.querySelector('a');
    expect(source.getAttribute('href')).toBe(card.source_reference);
    expect(source.getAttribute('rel')).toContain('noopener');
  });
});
