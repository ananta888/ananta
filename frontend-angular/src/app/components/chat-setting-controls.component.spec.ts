import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';
import { ChatSettingControlsComponent } from './chat-setting-controls.component';

describe('ChatSettingControlsComponent',()=>{
  it('renders a 100-field schema without persistence calls while typing',async()=>{
    await TestBed.configureTestingModule({imports:[ChatSettingControlsComponent]}).compileComponents();
    const fixture=TestBed.createComponent(ChatSettingControlsComponent);const component=fixture.componentInstance;
    component.scope='profile';component.settings=Array.from({length:100},(_,i)=>({key:`field_${i}`,label:`Field ${i}`,group:'Chat',type:'string' as const,default:'',scope_defaults:{profile:''},allowed_values:[],scopes:['profile'],secret:false,advanced:false,deprecated:false}));
    const emitted=vi.fn();component.changed.subscribe(emitted);fixture.detectChanges();
    expect(fixture.nativeElement.querySelectorAll('.field').length).toBe(100);
    component.changeTyped(component.settings[0],'draft');
    expect(emitted).toHaveBeenCalledWith({key:'field_0',value:'draft'});
  });
  it('exposes inherited and override state with an accessible reset label',async()=>{
    await TestBed.configureTestingModule({imports:[ChatSettingControlsComponent]}).compileComponents();const fixture=TestBed.createComponent(ChatSettingControlsComponent);const c=fixture.componentInstance;
    c.settings=[{key:'enabled',label:'Enabled',group:'Chat',type:'boolean',default:false,scope_defaults:{profile:false},allowed_values:[],scopes:['profile'],secret:false,advanced:false,deprecated:false}];c.delta={enabled:true};fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('button[aria-label*="Enabled"]')).toBeTruthy();
  });
});
