import { Injectable } from '@angular/core';

export interface ParsedGeoMapCsv {
  rows: Array<Record<string, unknown>>;
  columns: string[];
  suggestedRegionKey: string;
  suggestedValueKey: string;
}

@Injectable({ providedIn: 'root' })
export class GeoMapCsvParser {
  parse(text: string): ParsedGeoMapCsv {
    const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/).filter(line => line.trim());
    if (lines.length < 2) throw new Error('CSV benötigt Kopfzeile und mindestens eine Datenzeile.');
    if (lines.length > 100_001) throw new Error('CSV überschreitet 100000 Datenzeilen.');

    const delimiter = lines[0].includes(';') && !lines[0].includes(',') ? ';' : ',';
    const headers = this.parseLine(lines[0], delimiter);
    if (!headers.length || headers.some(header => !header) || new Set(headers).size !== headers.length) {
      throw new Error('CSV-Kopfzeilen müssen befüllt und eindeutig sein.');
    }
    const rows = lines.slice(1).map((line, rowIndex) => {
      const values = this.parseLine(line, delimiter);
      if (values.length !== headers.length) {
        throw new Error(`CSV-Zeile ${rowIndex + 2} hat eine falsche Spaltenzahl.`);
      }
      return Object.fromEntries(headers.map((header, index) => [header, values[index]]));
    });
    return {
      rows,
      columns: headers,
      suggestedRegionKey: this.suggest(headers, /^(region|region_id|iso|iso3|code|bundesland)$/i) || headers[0],
      suggestedValueKey: this.suggest(headers, /^(value|wert|amount|anzahl|count)$/i) || headers[1] || headers[0],
    };
  }

  private parseLine(line: string, delimiter: string): string[] {
    const values: string[] = [];
    let value = '';
    let quoted = false;
    for (let index = 0; index < line.length; index += 1) {
      const char = line[index];
      if (char === '"' && quoted && line[index + 1] === '"') { value += '"'; index += 1; }
      else if (char === '"') quoted = !quoted;
      else if (char === delimiter && !quoted) { values.push(value.trim()); value = ''; }
      else value += char;
    }
    if (quoted) throw new Error('CSV enthält ein nicht geschlossenes Anführungszeichen.');
    values.push(value.trim());
    return values;
  }

  private suggest(columns: string[], pattern: RegExp): string {
    return columns.find(column => pattern.test(column)) || '';
  }
}
