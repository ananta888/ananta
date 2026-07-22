import { ɵresolveComponentResources } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { SfuBroadcastVideoRenderService } from '../../services/sfu-broadcast-video-render.service';
import { SemanticRemoteVideoComponent } from './semantic-remote-video.component';

beforeAll(async () => {
  await ɵresolveComponentResources(resource => readFile(
    path.resolve(process.cwd(), 'src/app/features/voice', path.basename(String(resource))),
    'utf8',
  ));
});

describe('SemanticRemoteVideoComponent', () => {
  let fixture: ComponentFixture<SemanticRemoteVideoComponent>;
  const release = vi.fn();
  const renderer = { attach: vi.fn(() => release) };

  beforeEach(async () => {
    renderer.attach.mockReset(); renderer.attach.mockReturnValue(release); release.mockClear();
    await TestBed.configureTestingModule({
      imports: [SemanticRemoteVideoComponent],
      providers: [{ provide: SfuBroadcastVideoRenderService, useValue: renderer }],
    }).compileComponents();
    fixture = TestBed.createComponent(SemanticRemoteVideoComponent);
    fixture.componentRef.setInput('view', {
      handle: { handleId: 'sfu-video-1', source: 'camera' },
      accessibleName: 'Autorisierte Remote-Kamera', state: 'active',
    });
    fixture.detectChanges();
  });

  it('renders an accessible stable video frame through the scoped facade handle', () => {
    const video = fixture.nativeElement.querySelector('video') as HTMLVideoElement;
    expect(renderer.attach).toHaveBeenCalledWith(fixture.componentInstance.view, video);
    expect(video.getAttribute('aria-label')).toBe('Autorisierte Remote-Kamera');
    video.dispatchEvent(new Event('playing'));
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Wird wiedergegeben');
  });

  it('detaches listeners and SDK attachment idempotently on destroy', () => {
    fixture.destroy();
    expect(release).toHaveBeenCalledOnce();
  });

  it('detaches the old opaque handle before attaching a replacement', () => {
    const nextRelease = vi.fn();
    renderer.attach.mockReturnValueOnce(nextRelease);
    fixture.componentRef.setInput('view', {
      handle: { handleId: 'sfu-video-2', source: 'screen' },
      accessibleName: 'Autorisierte Remote-Bildschirmfreigabe', state: 'active',
    });
    fixture.detectChanges();
    expect(release).toHaveBeenCalledOnce();
    expect(renderer.attach).toHaveBeenCalledTimes(2);
    expect(fixture.nativeElement.textContent).not.toMatch(/alice|camera-a|publication/i);
  });
});
