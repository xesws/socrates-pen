/**
 * sidecar 版本闸与「按端口停」解析。跑的是 src/sidecar.ts 编译出来的真代码。
 *
 * 守三件事：
 *   1. ping 通但没有 version / 版本落后 → 不能标 Running（sidecarUsable）
 *   2. lsof -Fpc / netstat / ss 的 PID 解析，别把表头当 PID
 *   3. 本插件拉起 vs 旧 python vs 别的进程，谁被停掉要分得清
 */
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "sp-sidecar-")), "sidecar.cjs");
await build({
  entryPoints: ["src/sidecar.ts"],
  bundle: true,
  format: "cjs",
  platform: "node",
  external: ["obsidian"],
  outfile: out,
  logLevel: "error",
});

const require = createRequire(import.meta.url);
const Module = require("node:module");
const origLoad = Module._load;
Module._load = (req, parent, isMain) =>
  req === "obsidian"
    ? { getLanguage: () => "zh", Notice: class {}, PluginSettingTab: class {}, Setting: class {} }
    : origLoad(req, parent, isMain);
globalThis.window = { localStorage: { getItem: () => null } };
globalThis.document = { documentElement: { lang: "" } };

const mod = require(out);
const checks = [];
const check = (name, pass) => checks.push([name, Boolean(pass)]);

check("缺 version（0.16 health）不能干活", mod.sidecarUsable(undefined, "0.18.7") === false);
check("空 version 不能干活", mod.sidecarUsable("", "0.18.7") === false);
check("落后一版不能干活", mod.sidecarUsable("0.18.6", "0.18.7") === false);
check("对齐就能干活", mod.sidecarUsable("0.18.7", "0.18.7") === true);
check("sidecar 更新也能干活", mod.sidecarUsable("0.19.0", "0.18.7") === true);
check("cmpVer 同号为 0", mod.cmpVer("0.18.7", "0.18.7") === 0);

const lsof = `p12345
cPython
p99
cnode
`;
const fromLsof = mod.parseLsofF(lsof);
check(
  "lsof -Fpc 解出两个 PID + 命令",
  fromLsof.length === 2 &&
    fromLsof[0].pid === 12345 &&
    fromLsof[0].command === "Python" &&
    fromLsof[1].pid === 99 &&
    fromLsof[1].command === "node",
);
check("lsof 表头不是 PID", mod.parseLsofF("COMMAND PID USER\nPython 1").length === 0);

const netstat = [
  "  Proto  Local Address          Foreign Address        State           PID",
  "  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       4242",
  "  TCP    127.0.0.1:1234         0.0.0.0:0              LISTENING       7",
  "  TCP    127.0.0.1:8765         127.0.0.1:9999         ESTABLISHED     8",
].join("\n");
const fromNet = mod.parseNetstatAno(netstat, 8765);
check(
  "netstat 只收 LISTENING 且对端口",
  fromNet.length === 1 && fromNet[0].pid === 4242,
);
const netPrefix = [
  "  TCP    127.0.0.1:18765        0.0.0.0:0              LISTENING       99",
  "  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       4242",
].join("\n");
check(
  "netstat 端口不是后缀匹配（:8765 不收 :18765）",
  mod.parseNetstatAno(netPrefix, 8765).length === 1 &&
    mod.parseNetstatAno(netPrefix, 8765)[0].pid === 4242 &&
    mod.parseNetstatAno(netPrefix, 18765)[0].pid === 99,
);

const ss = 'LISTEN 0 2048 127.0.0.1:8765 0.0.0.0:* users:(("python",pid=5555,fd=3))';
const fromSs = mod.parseSsLtnp(ss);
check("ss -ltnp 解出 python pid", fromSs.length === 1 && fromSs[0].pid === 5555 && fromSs[0].command === "python");

check("owned 归 owned", mod.classifyListener("Python", true) === "owned");
check("python 残留归 leftover", mod.classifyListener("Python", false) === "leftover");
check("别的命令归 other", mod.classifyListener("nginx", false) === "other");

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`${pass ? "  ok  " : "  FAIL"} ${name}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
