import { Injectable, signal } from '@angular/core';
import { ValidationResult, VpGraph } from './visual-process-api.service';
import { emptyGraph } from './vp-editor-config';

@Injectable()
export class VpEditorStateFacade{
  readonly graph=signal<VpGraph>(emptyGraph());readonly selectedId=signal<string|null>(null);readonly dirty=signal(false);
  readonly validation=signal<ValidationResult|null>(null);readonly edgeMode=signal(false);readonly edgeSourceId=signal<string|null>(null);
  initialize(graph:VpGraph):void{this.graph.set(structuredClone(graph));this.selectedId.set(null);this.dirty.set(false);this.validation.set(null);}
  destroy():void{this.selectedId.set(null);this.edgeMode.set(false);this.edgeSourceId.set(null);}
}
