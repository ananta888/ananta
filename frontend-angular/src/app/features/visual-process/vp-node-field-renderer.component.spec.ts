import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { VpNodeFieldRendererComponent } from './vp-node-field-renderer.component';
import { VpNodeFieldDefinition, VpNodeFieldType } from './vp-node-definition-registry.service';

beforeAll(async () => {
  await ɵresolveComponentResources(resource => readFile(new URL(resource, import.meta.url), 'utf8'));
});

describe('VpNodeFieldRendererComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [VpNodeFieldRendererComponent] }).compileComponents();
  });

  function render(type: VpNodeFieldType, overrides: Partial<VpNodeFieldDefinition> = {}, value: unknown = null) {
    const fixture = TestBed.createComponent(VpNodeFieldRendererComponent);
    fixture.componentInstance.field = {
      path: `/metadata/${type}`,
      label: type,
      type,
      description: `${type} help`,
      ...overrides,
    };
    fixture.componentInstance.value = value;
    fixture.componentInstance.options = overrides.options ?? [];
    fixture.componentInstance.artifactKinds = ['text', 'json'];
    fixture.detectChanges();
    return fixture;
  }

  it('renders every registry field type without inspector-shell changes', async () => {
    const cases: Array<[VpNodeFieldType, Partial<VpNodeFieldDefinition>, unknown, string]> = [
      ['text', {}, 'value', 'input[type="text"]'],
      ['textarea', {}, 'value', 'textarea:not(.vpe-structured-input)'],
      ['number', {}, 2, 'input[type="number"]'],
      ['boolean', {}, true, 'input[type="checkbox"]'],
      ['enum', { options: [{ label: 'A', value: 'a' }] }, 'a', 'select'],
      ['multi-select', { options: [{ label: 'A', value: 'a' }] }, ['a'], 'fieldset input[type="checkbox"]'],
      ['resource-reference', { options: [{ label: 'Resource', value: 'resource-1' }] }, 'resource-1', 'select'],
      ['expression', {}, 'result.ok', 'input[type="text"]'],
      ['secret-reference', {}, 'credential-ref-1', 'input[type="password"]'],
      ['io-port', {}, [{ name: 'input', kind: 'text', required: true }], '[aria-label="Portname"]'],
      ['structured-list', {}, [{ key: 'value' }], '.vpe-structured-input'],
    ];
    for (const [type, overrides, value, selector] of cases) {
      const fixture = render(type, overrides, value);
      expect((fixture.nativeElement as HTMLElement).querySelector(selector), type).not.toBeNull();
      fixture.destroy();
    }
  });

  it('emits typed structured and IO changes but blocks read-only mutation', async () => {
    const fixture = render('structured-list', {}, []);
    const component = fixture.componentInstance;
    const changed = vi.fn();
    component.valueChanged.subscribe(changed);
    component.setStructuredDraft('[{"name":"row"}]');
    component.commitStructured();
    expect(changed).toHaveBeenCalledWith([{ name: 'row' }]);
    component.setStructuredDraft('{"not":"an array"}');
    component.commitStructured();
    expect(component.structuredError).toContain('JSON-Array');

    component.field = { ...component.field, readOnly: true };
    component.emitValue([{ name: 'blocked' }]);
    expect(changed).toHaveBeenCalledTimes(1);
  });
});
