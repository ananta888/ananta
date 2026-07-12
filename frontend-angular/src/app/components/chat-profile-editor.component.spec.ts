import { ComponentFixture, TestBed } from '@angular/core/testing';
import { BehaviorSubject } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';
import { ChatProfileEditorComponent } from './chat-profile-editor.component';
import { ChatSessionsService } from '../services/chat-sessions.service';

describe('ChatProfileEditorComponent', () => {
  function setup() {
    const service = {
      profiles$: new BehaviorSubject([{ id: 'builtin', name: 'Built-in', icon: '🎯', system_prompt: '', settings: { chat_backend: 'opencode' }, builtin: true }]),
      settingSchema$: new BehaviorSubject({ schema_version: 1, settings: [{ key: 'chat_backend', label: 'Backend', group: 'Modell', type: 'string', default: 'ananta-worker', scope_defaults: { profile: 'ananta-worker' }, allowed_values: ['ananta-worker', 'opencode'], scopes: ['profile'], secret: false, advanced: false, deprecated: false }] }),
      createProfile: vi.fn(), updateProfile: vi.fn(), deleteProfile: vi.fn(),
    };
    TestBed.configureTestingModule({ imports: [ChatProfileEditorComponent], providers: [{ provide: ChatSessionsService, useValue: service }] });
    const fixture: ComponentFixture<ChatProfileEditorComponent> = TestBed.createComponent(ChatProfileEditorComponent);
    fixture.detectChanges();
    return { fixture, component: fixture.componentInstance, service };
  }

  it('shows inherited values and keeps built-in profiles read-only', () => {
    const { component } = setup();
    component.select(component.profiles[0]);
    expect(component.readOnly).toBe(true);
    expect(component.effective(component.settings[0])).toBe('opencode');
  });

  it('duplicates built-ins without losing unknown forward-compatible settings', () => {
    const { component } = setup();
    component.profiles[0].settings['future_setting'] = 'preserved';
    component.select(component.profiles[0]);
    component.duplicate();
    expect(component.readOnly).toBe(false);
    expect(component.draft.settings['future_setting']).toBe('preserved');
  });

  it('removes one override without changing other profile values', () => {
    const { component } = setup();
    component.newProfile();
    component.setValue('chat_backend', 'opencode');
    component.setValue('chat_use_rag', true);
    component.resetValue('chat_backend');
    expect(component.hasOverride('chat_backend')).toBe(false);
    expect(component.draft.settings['chat_use_rag']).toBe(true);
  });
});
