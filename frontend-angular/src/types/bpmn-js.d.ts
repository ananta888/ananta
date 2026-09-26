declare module 'bpmn-js/lib/Modeler' {
  import type { BpmnModdle, ModdleElement } from 'bpmn-moddle';

  // Deliberately limited to the services consumed by the BPMN editor.
  export interface BpmnElement {
    id: string;
    type: string;
    businessObject: ModdleElement;
    labelTarget?: BpmnElement;
  }
  export interface BpmnWarning { message: string; }
  export interface BpmnEvents {
    'selection.changed': { newSelection: BpmnElement[] };
    'commandStack.changed': unknown;
  }
  export interface BpmnServices {
    eventBus: { on<K extends keyof BpmnEvents>(event: K, listener: (event: BpmnEvents[K]) => void): void };
    selection: { get(): BpmnElement[]; select(element: BpmnElement): void };
    elementRegistry: { get(id: string): BpmnElement | undefined };
    canvas: {
      zoom(value: 'fit-viewport'): void;
      addMarker(element: BpmnElement, marker: string): void;
      removeMarker(element: BpmnElement, marker: string): void;
    };
    modeling: { updateProperties(element: BpmnElement, properties: Record<string, unknown>): void };
    commandStack: { undo(): void; redo(): void; canUndo(): boolean; canRedo(): boolean };
    moddle: BpmnModdle;
  }
  class Modeler {
    constructor(options: { container: HTMLElement; moddleExtensions?: Record<string, unknown> });
    get<K extends keyof BpmnServices>(service: K): BpmnServices[K];
    importXML(xml: string): Promise<{ warnings: BpmnWarning[] }>;
    saveXML(options?: { format?: boolean }): Promise<{ xml?: string }>;
    destroy(): void;
  }
  export default Modeler;
}

declare module 'bpmn-moddle' {
  export interface ModdleElement {
    $type: string;
    id?: string;
    name?: string;
    value?: string;
    extensionElements?: ModdleElement;
    values?: ModdleElement[];
    rootElements?: ModdleElement[];
    flowElements?: ModdleElement[];
  }
  export class BpmnModdle {
    constructor(extensions?: Record<string, unknown>);
    create(type: string, properties?: Record<string, unknown>): ModdleElement;
    fromXML(xml: string): Promise<{ rootElement: ModdleElement; warnings: { message: string }[] }>;
    toXML(root: ModdleElement, options?: { format?: boolean }): Promise<{ xml: string }>;
  }
}
