import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Component, ɵresolveComponentResources } from '@angular/core';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { BehaviorSubject } from 'rxjs';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { ChatProfileEditorComponent } from './chat-profile-editor.component';
import { ChatSessionsService } from '../services/chat-sessions.service';
import { ChatProcessBindingEditorComponent } from './chat-process-binding-editor.component';
@Component({selector:'app-chat-process-binding-editor',standalone:true,template:''}) class ProcessBindingStub{}
beforeAll(async()=>{await ɵresolveComponentResources(resource=>readFile(path.resolve(process.cwd(),'src/app/features/visual-process',path.basename(String(resource))),'utf8'));});

describe('ChatProfileEditorComponent', () => {
  async function setup() {
    const service = {
      profiles$: new BehaviorSubject([{ id: 'builtin', name: 'Built-in', icon: '🎯', system_prompt: '', settings: { chat_backend: 'opencode' }, builtin: true }]),
      settingSchema$: new BehaviorSubject({ schema_version: 1, settings: [{ key: 'chat_backend', label: 'Backend', group: 'Modell', type: 'string', default: 'ananta-worker', scope_defaults: { profile: 'ananta-worker' }, allowed_values: ['ananta-worker', 'opencode'], scopes: ['profile'], secret: false, advanced: false, deprecated: false }] }),
      createProfile: vi.fn(), updateProfile: vi.fn(), deleteProfile: vi.fn(),
    };
    TestBed.overrideComponent(ChatProfileEditorComponent,{remove:{imports:[ChatProcessBindingEditorComponent]},add:{imports:[ProcessBindingStub]}});
    TestBed.configureTestingModule({ imports: [ChatProfileEditorComponent], providers: [{ provide: ChatSessionsService, useValue: service }] });
    await TestBed.compileComponents();
    const fixture: ComponentFixture<ChatProfileEditorComponent> = TestBed.createComponent(ChatProfileEditorComponent);
    fixture.detectChanges();
    return { fixture, component: fixture.componentInstance, service };
  }

  it('shows inherited values and keeps built-in profiles read-only', async () => {
    const { component } = await setup();
    component.select(component.profiles[0]);
    expect(component.readOnly).toBe(true);
    expect(component.effective(component.settings[0])).toBe('opencode');
  });

  it('duplicates built-ins without losing unknown forward-compatible settings', async () => {
    const { component } = await setup();
    component.profiles[0].settings['future_setting'] = 'preserved';
    component.select(component.profiles[0]);
    component.duplicate();
    expect(component.readOnly).toBe(false);
    expect(component.draft.settings['future_setting']).toBe('preserved');
  });

  it('removes one override without changing other profile values', async () => {
    const { component } = await setup();
    component.newProfile();
    component.setValue('chat_backend', 'opencode');
    component.setValue('chat_use_rag', true);
    component.resetValue('chat_backend');
    expect(component.hasOverride('chat_backend')).toBe(false);
    expect(component.draft.settings['chat_use_rag']).toBe(true);
  });
});
