import type { ModdleElement } from 'bpmn-moddle';

/** XML-owned metadata: moddle serialization and command-stack undo stay authoritative. */
export const ANANTA_MODDLE = {
  name: 'Ananta', uri: 'https://ananta.local/bpmn', prefix: 'ananta',
  xml: { tagAlias: 'lowerCase' },
  types: [{
    name: 'Metadata', superClass: ['Element'],
    properties: [{ name: 'value', type: 'String', isBody: true }],
  }],
};

interface ExtensionValue { $type: string; value?: string; }
interface BusinessObject { extensionElements?: { values?: ExtensionValue[] }; }
interface ModdlePort { create(type: string, properties: Record<string, unknown>): ModdleElement; }

export function readBpmnMetadata(object: BusinessObject): Record<string, unknown> {
  const entries = object.extensionElements?.values?.filter(value => value.$type === 'ananta:Metadata') || [];
  if (entries.length > 1) throw new Error('Mehrdeutige Ananta-Metadaten');
  if (!entries.length) return {};
  const parsed: unknown = JSON.parse(entries[0].value || '{}');
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Metadaten müssen ein JSON-Objekt sein');
  return parsed as Record<string, unknown>;
}

export function bpmnMetadataExtension(moddle: ModdlePort, object: BusinessObject, patch: Record<string, unknown>): ModdleElement {
  const metadata = { ...readBpmnMetadata(object), ...patch };
  const others = (object.extensionElements?.values || []).filter(value => value.$type !== 'ananta:Metadata');
  return moddle.create('bpmn:ExtensionElements', {
    values: [...others, moddle.create('ananta:Metadata', { value: JSON.stringify(metadata) })],
  });
}
