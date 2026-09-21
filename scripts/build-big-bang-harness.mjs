/** Build a disposable browser harness using the production view, API and CSS. */
import {build} from 'esbuild';
import {writeFileSync,copyFileSync,mkdirSync} from 'node:fs';
import {resolve,join} from 'node:path';
const dir=process.argv[2];if(!dir)throw new Error('Pass an output directory');mkdirSync(dir,{recursive:true});
await build({entryPoints:['scripts/fixtures/big-bang-browser.ts'],bundle:true,format:'iife',outfile:join(dir,'harness.js'),logLevel:'error',plugins:[{name:'host-double',setup(b){
  b.onResolve({filter:/^obsidian$/},()=>({path:resolve('scripts/fixtures/obsidian-browser.ts')}));
  b.onResolve({filter:/\/sidecar$/},()=>({path:'sidecar',namespace:'host-double'}));
  b.onLoad({filter:/.*/,namespace:'host-double'},()=>({contents:'export const sidecarUsable=(v,p)=>v===p; export class SidecarManager {}',loader:'js'}));
}}]});
copyFileSync('styles.css',join(dir,'styles.css'));
writeFileSync(join(dir,'index.html'),`<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="styles.css"><style>
:root{--background-primary:#fff;--background-secondary:#f4f4f6;--background-primary-alt:#fafafa;--background-modifier-border:#d9d9df;--background-modifier-hover:#eeeeef;--text-normal:#24242b;--text-muted:#686872;--text-faint:#999;--text-error:#b22;--text-accent:#705dcf;--text-accent-hover:#5842c3;--text-on-accent:white;--interactive-accent:#705dcf;--font-monospace:monospace;--font-text:system-ui;--text-warning:#a76a12}
body.dark{--background-primary:#202027;--background-secondary:#292932;--background-primary-alt:#25252e;--background-modifier-border:#444450;--background-modifier-hover:#33333f;--text-normal:#e4e4ed;--text-muted:#aaaabb;--text-faint:#888}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;font-family:system-ui;background:var(--background-primary);color:var(--text-normal)}button,input{font:inherit;color:inherit}button{cursor:pointer;border:1px solid var(--background-modifier-border);border-radius:5px;background:var(--background-secondary);padding:5px 9px}button:disabled{opacity:.4;cursor:default}input{background:var(--background-primary);border:1px solid var(--background-modifier-border);border-radius:5px;padding:5px}#workspace{height:100%;width:100%}
</style></head><body><main id="workspace"></main><script src="harness.js"></script></body></html>`);
