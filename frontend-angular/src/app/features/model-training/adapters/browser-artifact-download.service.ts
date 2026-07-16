import { DOCUMENT } from '@angular/common';
import { Injectable, inject } from '@angular/core';

import { AdapterExportDownload } from '../model-training.models';

@Injectable({ providedIn: 'root' })
export class BrowserArtifactDownloadService {
  private readonly document = inject(DOCUMENT);

  save(download: AdapterExportDownload): void {
    const objectUrl = URL.createObjectURL(download.blob);
    const link = this.document.createElement('a');
    link.href = objectUrl;
    link.download = download.filename;
    link.rel = 'noopener';
    link.hidden = true;
    this.document.body.appendChild(link);
    try {
      link.click();
    } finally {
      link.remove();
      URL.revokeObjectURL(objectUrl);
    }
  }
}
