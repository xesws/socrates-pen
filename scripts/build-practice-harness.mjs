/** Build a disposable browser harness using the production PracticeView and CSS. */
import { build } from "esbuild";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const dir = process.argv[2];
if (!dir) throw new Error("Pass an output directory");
mkdirSync(dir, { recursive: true });

await build({
  entryPoints: ["scripts/fixtures/practice-browser.ts"],
  bundle: true,
  format: "iife",
  outfile: join(dir, "harness.js"),
  logLevel: "error",
  plugins: [
    {
      name: "host-double",
      setup(b) {
        b.onResolve({ filter: /^obsidian$/ }, () => ({
          path: resolve("scripts/fixtures/obsidian-browser.ts"),
        }));
        b.onResolve({ filter: /\/sidecar$/ }, () => ({ path: "sidecar", namespace: "host-double" }));
        b.onLoad({ filter: /.*/, namespace: "host-double" }, () => ({
          contents: "export const sidecarUsable=(v,p)=>v===p;",
          loader: "js",
        }));
      },
    },
  ],
});
copyFileSync("styles.css", join(dir, "styles.css"));
writeFileSync(join(dir, "index.html"), `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="styles.css"><style>
:root{--background-primary:#fff;--background-secondary:#f4f4f6;--background-primary-alt:#fafafa;--background-modifier-border:#d9d9df;--background-modifier-hover:#eeeeef;--text-normal:#24242b;--text-muted:#686872;--text-faint:#999;--text-error:#b22;--text-success:#1b7f41;--text-warning:#a76a12;--text-accent:#705dcf;--text-accent-hover:#5842c3;--text-on-accent:white;--interactive-accent:#705dcf;--font-monospace:monospace;--font-text:system-ui;--font-interface:system-ui;--font-ui-large:20px;--font-ui-medium:15px;--font-semibold:600;--radius-s:5px;--size-2-1:2px;--size-4-1:4px;--size-4-2:8px;--size-4-3:12px;--size-4-4:16px}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;font-family:system-ui;background:var(--background-primary);color:var(--text-normal)}button,input,textarea{font:inherit;color:inherit}button{cursor:pointer;border:1px solid var(--background-modifier-border);border-radius:5px;background:var(--background-secondary);padding:5px 9px}button.mod-cta{background:var(--interactive-accent);color:var(--text-on-accent)}button:disabled{opacity:.4;cursor:default}input,textarea{background:var(--background-primary);border:1px solid var(--background-modifier-border);border-radius:5px;padding:5px}#workspace{height:100%;width:100%}
</style></head><body><main id="workspace"></main><script src="harness.js"></script></body></html>`);
