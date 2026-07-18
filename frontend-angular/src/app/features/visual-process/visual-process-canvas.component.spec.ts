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
  it('uses unique marker IDs for parallel canvas instances',async()=>{
    await TestBed.configureTestingModule({imports:[VisualProcessCanvasComponent],providers:[VpCanvasInteractionService]}).compileComponents();
    const first=TestBed.createComponent(VisualProcessCanvasComponent);
    first.componentInstance.graph={id:'vp-a',name:'Flow A',description:'',version:'1',tags:[],steps:[],edges:[]};first.detectChanges();
    const second=TestBed.createComponent(VisualProcessCanvasComponent);
    second.componentInstance.graph={id:'vp-b',name:'Flow B',description:'',version:'1',tags:[],steps:[],edges:[]};second.detectChanges();
    const firstIds=Array.from((first.nativeElement as HTMLElement).querySelectorAll('marker')).map(marker=>marker.id);
    const secondIds=Array.from((second.nativeElement as HTMLElement).querySelectorAll('marker')).map(marker=>marker.id);
    expect(firstIds).toHaveLength(2);expect(secondIds).toHaveLength(2);
    expect(firstIds.every(id=>!secondIds.includes(id))).toBe(true);
  });
  it('exposes equivalent keyboard targets for the canvas, nodes, ports and badges',async()=>{
    await TestBed.configureTestingModule({imports:[VisualProcessCanvasComponent],providers:[VpCanvasInteractionService]}).compileComponents();
    const fixture=TestBed.createComponent(VisualProcessCanvasComponent);const c=fixture.componentInstance;
    c.graph={id:'vp',name:'Flow',description:'',version:'1',tags:[],edges:[],steps:[{id:'s',label:'Step',kind:'task',io:{inputs:[{name:'prompt',kind:'text',required:true}],outputs:[]},position:{x:0,y:0},policy_hints:[],gate:false}]};
    c.validationIssues=[{severity:'error',code:'required',message:'Fehlt',step_id:'s'}];
    const selected=vi.fn();c.targetSelected.subscribe(selected);fixture.detectChanges();
    const wrap=(fixture.nativeElement as HTMLElement).querySelector('.vpe-canvas-wrap') as HTMLElement;
    wrap.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
    ((fixture.nativeElement as HTMLElement).querySelector('[data-semantic-kind="node_port"]') as HTMLElement)
      .dispatchEvent(new KeyboardEvent('keydown',{key:' ',bubbles:true}));
    ((fixture.nativeElement as HTMLElement).querySelector('[data-semantic-kind="validation_badge"]') as HTMLElement)
      .dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
    expect(selected.mock.calls.map(call=>call[0].kind)).toEqual(['canvas_region','node_port','validation_badge']);
    selected.mockClear();
    ((fixture.nativeElement as HTMLElement).querySelector('[data-semantic-kind="node_port"]') as HTMLElement)
      .dispatchEvent(new MouseEvent('click',{bubbles:true}));
    expect(selected).toHaveBeenCalledWith(expect.objectContaining({kind:'node_port',stepId:'s'}));
  });
  it('cancels touch hover context when the pointer interaction ends',async()=>{
    await TestBed.configureTestingModule({imports:[VisualProcessCanvasComponent],providers:[VpCanvasInteractionService]}).compileComponents();
    const fixture=TestBed.createComponent(VisualProcessCanvasComponent);const component=fixture.componentInstance;
    component.graph={id:'vp-touch',name:'Touch',description:'',version:'1',tags:[],edges:[],steps:[]};
    const previews=vi.fn();component.targetPreviewed.subscribe(previews);fixture.detectChanges();
    const touchEnd=new Event('pointerup') as PointerEvent;
    Object.defineProperty(touchEnd,'pointerType',{value:'touch'});
    component.pointerUp(touchEnd);
    expect(previews).toHaveBeenLastCalledWith(null);
  });
});
