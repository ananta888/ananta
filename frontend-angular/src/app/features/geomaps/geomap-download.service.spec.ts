import { GeoMapDownloadService } from './geomap-download.service';

describe('GeoMapDownloadService', () => {
  it('downloads decoded Hub artifacts and always releases the object URL', () => {
    const revoke = vi.fn();
    vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:geomap'), revokeObjectURL: revoke });
    const click = vi.fn();
    vi.spyOn(document, 'createElement').mockReturnValue({ click } as unknown as HTMLAnchorElement);
    new GeoMapDownloadService().download({
      schema: 'ananta.geomap-export-artifact.v1', filename: 'map.svg', media_type: 'image/svg+xml',
      content_base64: btoa('<svg/>'), metadata: {}, publication_eligible: true,
    });
    expect(click).toHaveBeenCalledOnce();
    expect(revoke).toHaveBeenCalledWith('blob:geomap');
  });
});
