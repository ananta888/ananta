import { Injectable } from '@angular/core';
import {
  MarkdownSlideExportCapability,
  MarkdownSlideExportFormat,
  MarkdownSlideExportJob,
  MarkdownSlideExportJobRequest,
} from './markdown-slide.models';

@Injectable({ providedIn: 'root' })
export class MarkdownSlideExportService {
  readonly allowedFormats: MarkdownSlideExportFormat[] = ['html', 'pdf', 'pptx'];

  capability(): MarkdownSlideExportCapability {
    return {
      supportedFormats: [],
      disabledReason: 'Export is intentionally disabled until a Hub-governed backend/worker export job API is available.',
    };
  }

  async createExportJob(_request: MarkdownSlideExportJobRequest): Promise<MarkdownSlideExportJob> {
    return {
      jobId: '',
      status: 'unsupported',
      logs: [],
      warnings: [],
      error: this.capability().disabledReason,
    };
  }
}
