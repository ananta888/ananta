import { describe, expect, it } from 'vitest';
import { BpmnModdle } from 'bpmn-moddle';
import { ANANTA_MODDLE, bpmnMetadataExtension, readBpmnMetadata } from './bpmn-metadata';

describe('BPMN metadata XML contract', () => {
  it('roundtrips actual moddle XML, including unknown metadata fields', async () => {
    const moddle = new BpmnModdle({ ananta: ANANTA_MODDLE });
    const xml = '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" targetNamespace="urn:test"><process id="p"><serviceTask id="task"/></process></definitions>';
    const { rootElement } = await moddle.fromXML(xml);
    const task = rootElement.rootElements[0].flowElements[0];
    task.extensionElements = bpmnMetadataExtension(moddle, task, {
      role: 'analyst', gate: false, allowed_tools: ['read_file'], policy_scope: { source: 'synthetic' },
      io: { inputs: [{ name: 'request' }] }, future_field: { retained: true },
    });
    task.extensionElements = bpmnMetadataExtension(moddle, task, { role: 'reviewer' });
    const exported = await moddle.toXML(rootElement);
    expect(exported.xml).toContain('ananta:metadata');
    const imported = await new BpmnModdle({ ananta: ANANTA_MODDLE }).fromXML(exported.xml);
    expect(imported.warnings).toEqual([]);
    expect(readBpmnMetadata(imported.rootElement.rootElements[0].flowElements[0])).toEqual({
      role: 'reviewer', gate: false, allowed_tools: ['read_file'], policy_scope: { source: 'synthetic' },
      io: { inputs: [{ name: 'request' }] }, future_field: { retained: true },
    });
  });

  it('refuses malformed or duplicate metadata instead of silently clearing it', () => {
    expect(() => readBpmnMetadata({ extensionElements: { values: [{ $type: 'ananta:Metadata', value: '[]' }] } })).toThrow();
    expect(() => readBpmnMetadata({ extensionElements: { values: [
      { $type: 'ananta:Metadata', value: '{}' }, { $type: 'ananta:Metadata', value: '{}' },
    ] } })).toThrow();
  });
});
