import { signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { describe, expect, it, vi } from 'vitest';

import { OrganizationBlueprintSummary } from '../models/organization-topology.models';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';
import { OrganizationSetupComponent } from './organization-setup.component';

describe('OrganizationSetupComponent standard selector binding', () => {
  it('keeps blueprint and team count synchronized in both selection directions', () => {
    const { component, blueprints } = createComponent();
    const enterprise5 = blueprints.find(item => item.key === 'enterprise:standard:5')!;
    const enterprise8 = blueprints.find(item => item.key === 'enterprise:standard:8')!;
    const alternative5 = blueprints.find(item => item.key === 'alternative:standard:5')!;
    const alternative8 = blueprints.find(item => item.key === 'alternative:standard:8')!;

    expect(component.blueprintKey).toBe(enterprise8.key);
    expect(component.teamCount).toBe(8);

    component.selectBlueprint(enterprise5.key);
    expect(component.blueprintKey).toBe(enterprise5.key);
    expect(component.teamCount).toBe(5);

    component.selectForCount(8);
    expect(component.blueprintKey).toBe(enterprise8.key);
    expect(component.teamCount).toBe(8);

    component.selectBlueprint(alternative5.key);
    component.selectForCount(8);
    expect(component.blueprintKey).toBe(alternative8.key);
    expect(component.teamCount).toBe(8);
  });

  it('refuses mismatches and compiles only an atomically selected server summary', () => {
    const { component, compile } = createComponent();
    component.blueprintKey = 'enterprise:standard:5';
    component.teamCount = 8;

    component.compile();

    expect(component.canCompile()).toBe(false);
    expect(compile).not.toHaveBeenCalled();

    component.selectBlueprint('enterprise:standard:5');
    component.compile();

    expect(compile).toHaveBeenCalledWith({
      blueprint_key: 'enterprise:standard:5',
      title: 'Enterprise Produktorganisation',
      team_count: 5,
    });
    expect(component.teamCount).toBe(5);
  });

  it('binds the blueprint dropdown to the atomic selector handler', () => {
    const { component, fixture } = createComponent();
    const blueprintSelect = fixture.debugElement.query(By.css('select[name="blueprint"]'));

    blueprintSelect.triggerEventHandler('ngModelChange', 'enterprise:standard:5');
    fixture.detectChanges();

    expect(component.teamCount).toBe(5);
    const options = Array.from(
      fixture.nativeElement.querySelectorAll('select[name="blueprint"] option'),
    ) as HTMLOptionElement[];
    expect(options).toHaveLength(2);
    expect(options.every(option => option.textContent?.includes('5 Teams'))).toBe(true);
  });
});

function createComponent(): {
  component: OrganizationSetupComponent;
  fixture: ComponentFixture<OrganizationSetupComponent>;
  blueprints: readonly OrganizationBlueprintSummary[];
  compile: ReturnType<typeof vi.fn>;
} {
  const blueprints = [
    blueprint('alternative', 5),
    blueprint('enterprise', 5),
    blueprint('alternative', 8),
    blueprint('enterprise', 8, true),
  ];
  const compile = vi.fn();
  const state = {
    projectId: signal('project-alpha'),
    blueprints: signal<readonly OrganizationBlueprintSummary[]>(blueprints),
    compilePlan: signal(null),
    mutating: signal(false),
    compile,
    compileCustom: vi.fn(),
    instantiate: vi.fn(),
  };
  TestBed.configureTestingModule({
    imports: [OrganizationSetupComponent],
    providers: [{ provide: OrganizationTopologyStateService, useValue: state }],
  });
  const fixture = TestBed.createComponent(OrganizationSetupComponent);
  fixture.detectChanges();
  return { component: fixture.componentInstance, fixture, blueprints, compile };
}

function blueprint(
  definitionKey: string,
  teamCount: number,
  recommended = false,
): OrganizationBlueprintSummary {
  return {
    key: `${definitionKey}:standard:${teamCount}`,
    definition_key: definitionKey,
    version: '1',
    title: `${definitionKey} · ${teamCount} Teams`,
    team_count: teamCount,
    standard: true,
    recommended,
    test_only: false,
    revision: `${definitionKey}-revision`,
    supported_team_counts: [5, 8],
    custom_team_count_min: 2,
    custom_team_count_max: 10,
    custom_team_blueprints: [],
  };
}
