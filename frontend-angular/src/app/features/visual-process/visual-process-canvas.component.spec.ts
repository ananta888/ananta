import { ɵresolveComponentResources } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import { beforeAll,describe,expect,it,vi } from 'vitest';
import { VisualProcessCanvasComponent } from './visual-process-canvas.component';
import { VpCanvasInteractionService } from './vp-canvas-interaction.service';
beforeAll(async()=>{await ɵresolveComponentResources(resource=>readFile(new URL(resource,import.meta.url),'utf8'));});

describe('VisualProcessCanvasComponent',()=>{
  it('uses the same canvas in read-only mode and suppresses graph mutation gestures',async()=>{
    await TestBed.configureTestingModule({imports:[VisualProcessCanvasComponent],providers:[VpCanvasInteractionService]}).compileComponents();const fixture=TestBed.createComponent(VisualProcessCanvasComponent);const c=fixture.componentInstance;
    c.graph={id:'vp',name:'Flow',description:'',version:'1',tags:[],edges:[],steps:[{id:'s',label:'Step',kind:'task',io:{inputs:[],outputs:[]},position:{x:0,y:0},policy_hints:[],gate:false}]};c.readOnly=true;c.mode='compact-readonly';const emitted=vi.fn();c.nodeMouseDown.subscribe(emitted);fixture.detectChanges();c.nodeDown(new MouseEvent('mousedown'),'s');
    expect(emitted).not.toHaveBeenCalled();expect(fixture.nativeElement.querySelector('[data-run-state]')).toBeTruthy();
  });
  it('renders textual runtime state for non-colour accessibility',async()=>{
    await TestBed.configureTestingModule({imports:[VisualProcessCanvasComponent],providers:[VpCanvasInteractionService]}).compileComponents();const fixture=TestBed.createComponent(VisualProcessCanvasComponent);const c=fixture.componentInstance;
    c.graph={id:'vp',name:'Flow',description:'',version:'1',tags:[],edges:[],steps:[{id:'s',label:'Step',kind:'task',io:{inputs:[],outputs:[]},position:{x:0,y:0},policy_hints:[],gate:false}]};c.runtimeOverlay={run_id:'r',workflow_id:'r',overall_status:'running',current_step_ids:['s'],updated_at:1,steps:{s:{step_id:'s',status:'awaiting_approval'}}};fixture.detectChanges();expect(fixture.nativeElement.textContent).toContain('awaiting_approval');
  });
});
