import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';
const dir = mkdtempSync(join(tmpdir(), 'sp-layout-'));
try {
  const file = join(dir, 'layout.cjs');
  await build({entryPoints:['src/agentlayout.ts'], bundle:true, platform:'node', format:'cjs', outfile:file, logLevel:'error'});
  const {computeAgentLayout: layout} = createRequire(import.meta.url)(file);
  for (const [width,height,expected] of [
    [1440,900,['single','row-2','row-3','grid']],
    [1100,800,['single','row-2','primary-left','grid']],
    [900,1440,['single','column-2','column-3','grid']],
    [800,1000,['single','column-2','primary-top','grid']],
    [700,1800,['single','column-2','column-3','column-4']],
    [320,900,['single','column-2','stack','stack']],
  ]) for (let count=1;count<=4;count++) {
    const got = layout({count,width,height});
    assert.equal(got.kind, expected[count-1], `${width}x${height}, ${count} agents`);
    assert.equal(got.areas.length,count);
  }
  // Size changes near the spacious threshold must not make the view jump.
  let previous=layout({count:3,width:1250,height:800});
  for(const width of [1277,1290,1269,1279]) {
    previous=layout({count:3,width,height:800,previous});
    assert.equal(previous.kind,'primary-left');
  }
  previous=layout({count:3,width:1400,height:800,previous});
  assert.equal(previous.kind,'row-3');
  assert.equal(layout({count:3,width:950,height:800,previous}).kind,'primary-left');
  assert.equal(layout({count:3,width:0,height:0,previous}),previous);
  assert.equal(layout({count:1,width:320,height:200,previous}).kind,'single');
  // For all sizes, overflow is explicit; no impossible 2x2 in a narrow sidebar.
  for(let width=240;width<=2000;width+=40) for(let height=240;height<=1800;height+=40) {
    for(let count=2;count<=4;count++) {
      const got=layout({count,width,height});
      if(got.kind==='grid'||got.kind.startsWith('primary')) assert.ok(width>=648&&height>=648);
      if(got.kind==='stack') assert.equal(got.overflow, height<count*320+(count-1)*8);
    }
  }
  console.log('Agent layout: dimensions, breakpoints, hysteresis and overflow passed');
} finally {rmSync(dir,{recursive:true,force:true});}
