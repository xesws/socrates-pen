import esbuild from "esbuild";
import process from "node:process";
import { copyFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const prod = process.argv[2] === "production";
const vaultDir = (process.env.VAULT_PLUGIN_DIR || "").trim();

if (!prod && !vaultDir) {
  console.error(
    "VAULT_PLUGIN_DIR is required for npm run dev.\n" +
      "Example:\n" +
      "  export VAULT_PLUGIN_DIR=/path/to/vault/.obsidian/plugins/socrates-pen\n" +
      "  npm run dev",
  );
  process.exit(1);
}

const outdir = prod ? root : resolve(vaultDir);
mkdirSync(outdir, { recursive: true });

function copyStatics(dest, { hotreload }) {
  if (resolve(dest) !== resolve(root)) {
    copyFileSync(join(root, "manifest.json"), join(dest, "manifest.json"));
    copyFileSync(join(root, "styles.css"), join(dest, "styles.css"));
  }
  if (hotreload) writeFileSync(join(dest, ".hotreload"), "");
}

const context = await esbuild.context({
  entryPoints: [join(root, "src/main.ts")],
  bundle: true,
  external: [
    "obsidian",
    "electron",
    "@codemirror/autocomplete",
    "@codemirror/collab",
    "@codemirror/commands",
    "@codemirror/language",
    "@codemirror/lint",
    "@codemirror/search",
    "@codemirror/state",
    "@codemirror/view",
    "@lezer/common",
    "@lezer/highlight",
    "@lezer/lr",
  ],
  format: "cjs",
  target: "es2018",
  logLevel: "info",
  sourcemap: prod ? false : "inline",
  treeShaking: true,
  outfile: join(outdir, "main.js"),
});

copyStatics(outdir, { hotreload: !prod });
if (prod) {
  await context.rebuild();
  await context.dispose();
} else {
  await context.watch();
}
