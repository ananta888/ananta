// Structured provider assets are observations, never Ananta evidence or grants.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const TYPES = new Set(['Gene', 'Capsule', 'EvolutionEvent']);
const VERSIONS = new Set(['1.6.0', '1.8.0', '1.12.1']); // bundled + current schemas in pinned v1.94.0
const MAX_BYTES = 256 * 1024;

export class GepCompatibilityError extends Error {}

function readFile(root, relative) {
  const target = path.join(root, relative);
  let stat;
  try { stat = fs.lstatSync(target); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  if (stat.isSymbolicLink() || !stat.isFile() || stat.size > MAX_BYTES ||
      !fs.realpathSync(target).startsWith(fs.realpathSync(root) + path.sep)) {
    throw new GepCompatibilityError('evolver_gep_artifact_path_or_size');
  }
  return fs.readFileSync(target, 'utf8');
}

function promptArtifacts(root, memoryDir) {
  const prompts = [];
  const legacy = readFile(root, 'gep_prompt.txt');
  if (legacy !== null) prompts.push({id: 'gep-prompt', text: legacy});
  if (memoryDir !== undefined) {
    const evolution = path.join(memoryDir, 'evolution');
    for (const directory of [memoryDir, evolution]) {
      let stat;
      try { stat = fs.lstatSync(directory); }
      catch (error) { if (error.code === 'ENOENT') return prompts; throw error; }
      if (stat.isSymbolicLink() || !stat.isDirectory()) {
        throw new GepCompatibilityError('evolver_gep_prompt_directory_invalid');
      }
    }
    const names = fs.readdirSync(evolution);
    if (names.length > 128) throw new GepCompatibilityError('evolver_gep_prompt_directory_budget');
    for (const name of names.sort()) {
      if (!/^gep_prompt_[A-Za-z0-9_#-]+\.txt$/.test(name) || name.length > 190) continue;
      prompts.push({id: name.slice(0, -4), text: readFile(evolution, name)});
    }
  }
  if (prompts.length > 1) throw new GepCompatibilityError('evolver_gep_prompt_budget');
  return prompts;
}

export function collectGepArtifacts(root, { baselineIds = [], memoryDir } = {}) {
  if (fs.lstatSync(root).isSymbolicLink() || !fs.lstatSync(root).isDirectory()) {
    throw new GepCompatibilityError('evolver_gep_root_invalid');
  }
  const rows = [];
  const seen = new Map();
  const baseline = new Set(baselineIds);
  for (const [relative, key, type] of [
    ['genes.json', 'genes', 'Gene'], ['capsules.json', 'capsules', 'Capsule'],
    ['genes.jsonl', null, 'Gene'], ['capsules.jsonl', null, 'Capsule'], ['events.jsonl', null, 'EvolutionEvent']
  ]) {
    const raw = readFile(root, relative);
    if (raw === null) continue;
    let values;
    try {
      if (key) {
        const envelope = JSON.parse(raw);
        if (!envelope || !Array.isArray(envelope[key])) throw new Error('envelope');
        if (envelope.version !== undefined && envelope.version !== (key === 'genes' ? 2 : 1)) {
          throw new Error('envelope_version');
        }
        if (envelope.schema_version !== undefined && !VERSIONS.has(envelope.schema_version)) {
          throw new Error('schema_version');
        }
        values = envelope[key];
      } else values = raw.split('\n').filter(s => s.trim()).map(s => JSON.parse(s));
    } catch (_) { throw new GepCompatibilityError('evolver_gep_json_incompatible'); }
    for (const asset of values) {
      if (!asset || typeof asset !== 'object' || Array.isArray(asset) || asset.type !== type ||
          !TYPES.has(asset.type) || typeof asset.id !== 'string' || !asset.id || asset.id.length > 200 ||
          asset.schema_version !== undefined && !VERSIONS.has(asset.schema_version)) {
        throw new GepCompatibilityError('evolver_gep_schema_incompatible');
      }
      const serialized = JSON.stringify(asset);
      if (Buffer.byteLength(serialized) > 32768) throw new GepCompatibilityError('evolver_gep_asset_too_large');
      const digest = crypto.createHash('sha256').update(serialized).digest('hex');
      if (seen.has(asset.id)) {
        if (seen.get(asset.id) !== digest) throw new GepCompatibilityError('evolver_gep_id_conflict');
        continue;
      }
      seen.set(asset.id, digest);
      rows.push({kind: asset.type, provider_asset_id: asset.id,
        provider_schema_version: asset.schema_version || 'legacy-pinned-v1.94.0',
        provider_payload_sha256: digest, proposal_candidate: !baseline.has(asset.id), raw_payload: asset,
        evidence_status: 'unverified', grants_permission: false});
      if (rows.length > 64) throw new GepCompatibilityError('evolver_gep_asset_budget');
      if (Buffer.byteLength(JSON.stringify(rows)) > 512 * 1024) throw new GepCompatibilityError('evolver_gep_total_budget');
    }
  }
  for (const prompt of promptArtifacts(root, memoryDir)) {
    if (prompt.text === null || Buffer.byteLength(prompt.text) > 30000) {
      throw new GepCompatibilityError('evolver_gep_asset_too_large');
    }
    rows.push({kind: 'GepPrompt', provider_asset_id: prompt.id,
      provider_schema_version: 'pinned-text-v1.94.0', proposal_candidate: true,
      raw_payload: {text: prompt.text}, evidence_status: 'unverified', grants_permission: false});
  }
  if (Buffer.byteLength(JSON.stringify(rows)) > 512 * 1024) {
    throw new GepCompatibilityError('evolver_gep_total_budget');
  }
  return rows;
}
