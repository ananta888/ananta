import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {collectGepArtifacts} from '../../services/evolver_bridge/gep_artifacts.js';
import {prepareWorkspace} from '../../services/evolver_bridge/workspace.js';
import {validateRuntime} from '../../services/evolver_bridge/runtime.js';

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

test('runtime prompt filenames are imported from the explicit memory directory',()=>fixture(root=>{
 const memoryDir=path.join(root,'memory'), evolution=path.join(memoryDir,'evolution');
 fs.mkdirSync(evolution,{recursive:true});
 fs.writeFileSync(path.join(evolution,'gep_prompt_Cycle_#0001_run_1.txt'),'runtime prompt');
 fs.writeFileSync(path.join(evolution,'unrelated.txt'),'ignored');
 const rows=collectGepArtifacts(root,{memoryDir});
 assert.equal(rows.length,1);
 assert.equal(rows[0].raw_payload.text,'runtime prompt');
 assert.equal(rows[0].provider_asset_id,'gep_prompt_Cycle_#0001_run_1');
 assert.equal(rows[0].grants_permission,false);
}));

test('runtime prompt directory and file symlinks fail closed',()=>fixture(root=>{
 const memoryDir=path.join(root,'memory'), evolution=path.join(memoryDir,'evolution');
 fs.mkdirSync(evolution,{recursive:true});
 fs.symlinkSync('/missing-prompt',path.join(evolution,'gep_prompt_cycle_run.txt'));
 assert.throws(()=>collectGepArtifacts(root,{memoryDir}),/path_or_size/);
 fs.rmSync(evolution,{recursive:true});
 fs.symlinkSync(root,evolution);
 assert.throws(()=>collectGepArtifacts(root,{memoryDir}),/directory_invalid/);
}));

test('multiple or oversized runtime prompts exceed the bounded import policy',()=>fixture(root=>{
 const memoryDir=path.join(root,'memory'), evolution=path.join(memoryDir,'evolution');
 fs.mkdirSync(evolution,{recursive:true});
 const first=path.join(evolution,'gep_prompt_cycle_run1.txt');
 fs.writeFileSync(first,'x'.repeat(30001));
 assert.throws(()=>collectGepArtifacts(root,{memoryDir}),/asset_too_large/);
 fs.writeFileSync(first,'first');
 fs.writeFileSync(path.join(evolution,'gep_prompt_cycle_run2.txt'),'second');
 assert.throws(()=>collectGepArtifacts(root,{memoryDir}),/prompt_budget/);
}));

test('pinned runtime writes a prompt the collector can preserve',{
 skip:!fs.existsSync('/opt/evolver-src/index.js') && process.env.ANANTA_REQUIRE_PINNED_EVOLVER !== '1',
 timeout:15000,
},()=>{
 const contract=JSON.parse(fs.readFileSync(new URL('../../services/evolver_bridge/runtime-contract.json',import.meta.url),'utf8'));
 validateRuntime('/opt/evolver-src',contract);
 const workspace=prepareWorkspace({objective:'offline synthetic compatibility check'});
 try {
  const memoryDir=path.join(workspace.root,'memory'), gepDir=path.join(workspace.root,'gep-assets');
  fs.mkdirSync(memoryDir); fs.mkdirSync(gepDir);
  execFileSync('node',['/opt/evolver-src/index.js','run'],{
   cwd:workspace.root,timeout:10000,maxBuffer:4*1024*1024,
   stdio:['ignore','pipe','pipe'],
   env:{PATH:'/usr/local/bin:/usr/bin:/bin',HOME:workspace.root,OPENCLAW_WORKSPACE:workspace.root,
    MEMORY_DIR:memoryDir,GEP_ASSETS_DIR:gepDir,EVOLVER_LOGS_DIR:path.join(workspace.root,'logs'),
    A2A_HUB_URL:'http://127.0.0.1:9',EVOMAP_HUB_URL:'http://127.0.0.1:9',
    GIT_CONFIG_NOSYSTEM:'1',GIT_CONFIG_GLOBAL:'/dev/null'},
  });
  const prompts=collectGepArtifacts(gepDir,{memoryDir}).filter(row=>row.kind==='GepPrompt');
  assert.equal(prompts.length,1);
  const evolution=path.join(memoryDir,'evolution');
  const filename=fs.readdirSync(evolution).find(name=>name.endsWith('.txt'));
  assert.equal(prompts[0].raw_payload.text,fs.readFileSync(path.join(evolution,filename),'utf8'));
 } finally {workspace.cleanup();}
});
