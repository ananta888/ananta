import { Injectable } from '@angular/core';

import { GeoMapExportArtifact } from './geomap.models';

@Injectable({ providedIn: 'root' })
export class GeoMapDownloadService {
  download(artifact: GeoMapExportArtifact): void {
    const binary = atob(artifact.content_base64);
    const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: artifact.media_type }));
    try {
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = artifact.filename;
      anchor.click();
    } finally {
      URL.revokeObjectURL(url);
    }
  }
}
