import { TestBed } from '@angular/core/testing';

import { BrowserArtifactDownloadService } from './browser-artifact-download.service';

describe('BrowserArtifactDownloadService', () => {
  it('saves the authenticated blob under the bounded server artifact filename', () => {
    const createObjectUrl = vi.fn(() => 'blob:adapter-export');
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectUrl });
    let clicked: HTMLAnchorElement | null = null;
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
      clicked = this;
    });

    TestBed.configureTestingModule({ providers: [BrowserArtifactDownloadService] });
    TestBed.inject(BrowserArtifactDownloadService).save({
      blob: new Blob(['zip'], { type: 'application/zip' }),
      filename: 'lora-export-1.zip',
      sha256: 'a'.repeat(64),
    });

    expect(createObjectUrl).toHaveBeenCalledOnce();
    expect(click).toHaveBeenCalledOnce();
    expect(clicked?.download).toBe('lora-export-1.zip');
    expect(clicked?.isConnected).toBe(false);
    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:adapter-export');
  });
});
