import {describe,expect,it} from 'vitest';
import {VpEditorStateFacade} from './vp-editor-state.facade';
describe('VpEditorStateFacade',()=>{it('keeps parallel embedded editor state isolated',()=>{const a=new VpEditorStateFacade(),b=new VpEditorStateFacade();const graph={id:'a',name:'A',description:'',version:'1',tags:[],steps:[],edges:[]};a.initialize(graph);a.selectedId.set('step');a.dirty.set(true);expect(b.selectedId()).toBeNull();expect(b.dirty()).toBe(false);expect(a.graph()).not.toBe(graph);a.destroy();expect(a.selectedId()).toBeNull();});});
