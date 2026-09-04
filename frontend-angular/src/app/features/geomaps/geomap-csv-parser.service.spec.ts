import { GeoMapCsvParser } from './geomap-csv-parser.service';

describe('GeoMapCsvParser', () => {
  const parser = new GeoMapCsvParser();

  it('parses quoted fields and suggests explicit region/value columns', () => {
    const result = parser.parse('region,value,label\nDE-BE,2,"Berlin, Stadt"\nDE-BB,4,Brandenburg\n');
    expect(result.rows[0]).toEqual({ region: 'DE-BE', value: '2', label: 'Berlin, Stadt' });
    expect(result.suggestedRegionKey).toBe('region');
    expect(result.suggestedValueKey).toBe('value');
  });

  it('rejects ambiguous or empty headers', () => {
    expect(() => parser.parse('region,region\nDE-BE,DE-BE')).toThrow(/eindeutig/);
    expect(() => parser.parse('region,\nDE-BE,2')).toThrow(/befüllt/);
  });
});
