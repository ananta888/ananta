import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {collectGepArtifacts} from '../../services/evolver_bridge/gep_artifacts.js';

function fixture(run) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'gep-test-'));
  try {run(root);} finally {fs.rmSync(root,{recursive:true,force:true});}
}

test('Gene, Capsule, Event and Prompt keep their separate type and unknown fields',()=>fixture(root=>{
 fs.writeFileSync(path.join(root,'genes.json'),JSON.stringify({genes:[{type:'Gene',id:'gene-one',schema_version:'1.12.1',unknown:{kept:true}}]}));
 fs.writeFileSync(path.join(root,'capsules.jsonl'),JSON.stringify({type:'Capsule',id:'capsule-one',gene:'gene-one'})+'\n');
 fs.writeFileSync(path.join(root,'events.jsonl'),JSON.stringify({type:'EvolutionEvent',id:'event-one',parent:'capsule-one'})+'\n');
 fs.writeFileSync(path.join(root,'gep_prompt.txt'),'synthetic proposal');
 const rows=collectGepArtifacts(root);
 assert.deepEqual(rows.map(r=>r.kind),['Gene','Capsule','EvolutionEvent','GepPrompt']);
 assert.deepEqual(rows[0].raw_payload.unknown,{kept:true});
 assert.equal(rows[0].evidence_status,'unverified');
 assert.equal(rows[0].grants_permission,false);
}));

test('bundled baseline genes are not newly generated candidates',()=>fixture(root=>{
 fs.writeFileSync(path.join(root,'genes.json'),JSON.stringify({genes:[{type:'Gene',id:'seed'}]}));
 assert.equal(collectGepArtifacts(root,{baselineIds:['seed']})[0].proposal_candidate,false);
}));

test('pinned runtime bundled historical schemas remain explicit compatibility cases',()=>fixture(root=>{
 const genes=['1.6.0','1.8.0'].map((version,i)=>({type:'Gene',id:`seed-${i}`,schema_version:version}));
 fs.writeFileSync(path.join(root,'genes.json'),JSON.stringify({version:2,genes}));
 const rows=collectGepArtifacts(root,{baselineIds:genes.map(g=>g.id)});
 assert.deepEqual(rows.map(r=>r.provider_schema_version),['1.6.0','1.8.0']);
 assert.equal(rows.some(r=>r.proposal_candidate),false);
 fs.writeFileSync(path.join(root,'genes.json'),JSON.stringify({version:99,genes}));
 assert.throws(()=>collectGepArtifacts(root),/json_incompatible/);
}));

test('unknown versions, types and duplicate conflicting IDs fail closed',()=>fixture(root=>{
 for (const asset of [{type:'Gene',id:'g',schema_version:'future'}, {type:'Other',id:'g'}]) {
  fs.writeFileSync(path.join(root,'genes.json'),JSON.stringify({genes:[asset]}));
  assert.throws(()=>collectGepArtifacts(root),/schema_incompatible/);
 }
 fs.writeFileSync(path.join(root,'genes.json'),JSON.stringify({genes:[{type:'Gene',id:'g'}]}));
 fs.writeFileSync(path.join(root,'genes.jsonl'),JSON.stringify({type:'Gene',id:'g',extra:true}));
 assert.throws(()=>collectGepArtifacts(root),/id_conflict/);
}));

test('malformed JSONL and symlink files cannot become proposals',()=>fixture(root=>{
 fs.writeFileSync(path.join(root,'events.jsonl'),'not-json\n');
 assert.throws(()=>collectGepArtifacts(root),/json_incompatible/);
 fs.unlinkSync(path.join(root,'events.jsonl'));
 fs.symlinkSync('/etc/passwd',path.join(root,'genes.json'));
 assert.throws(()=>collectGepArtifacts(root),/path_or_size/);
}));
