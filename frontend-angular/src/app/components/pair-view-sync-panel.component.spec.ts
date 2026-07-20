import { describe, expect, it, beforeEach } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { PairViewSyncPanelComponent } from './pair-view-sync-panel.component';
import { DEFAULT_PERMISSIONS } from '../services/pair-view-sync.types';

describe('PairViewSyncPanelComponent', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [PairViewSyncPanelComponent],
      providers: [provideRouter([]), provideNoopAnimations()],
    });
  });

  it('mounts and shows the create form by default', () => {
    const fixture = TestBed.createComponent(PairViewSyncPanelComponent);
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('Pair-Dev View-Sync');
    expect(text).toContain('Berechtigungen');
  });

  it('renders a checkbox for every documented permission', () => {
    const fixture = TestBed.createComponent(PairViewSyncPanelComponent);
    fixture.detectChanges();
    const checkboxes = (fixture.nativeElement as HTMLElement).querySelectorAll('input[type=checkbox]');
    expect(checkboxes.length).toBe(5);
  });

  it('uses the canonical Hub permission names and safe defaults', () => {
    const fixture = TestBed.createComponent(PairViewSyncPanelComponent);
    const cmp = fixture.componentInstance;
    expect(cmp.form.selected.chat).toBe(true);
    expect(cmp.form.selected.view_tui).toBe(true);
    expect(cmp.form.selected.artifact_share).toBe(true);
    expect(cmp.form.selected.remote_control).toBe(false);
    expect(cmp.form.selected.remote_cursor).toBe(false);
  });

  it('preserves the same default as the shared service default', () => {
    const fixture = TestBed.createComponent(PairViewSyncPanelComponent);
    const cmp = fixture.componentInstance;
    const d = DEFAULT_PERMISSIONS;
    expect(cmp.form.selected.chat).toBe(d.chat);
    expect(cmp.form.selected.view_tui).toBe(d.view_tui);
    expect(cmp.form.selected.remote_control).toBe(d.remote_control);
    expect(cmp.form.selected.remote_cursor).toBe(d.remote_cursor);
    expect(cmp.form.selected.artifact_share).toBe(d.artifact_share);
  });
});
