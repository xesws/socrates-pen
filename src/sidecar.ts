/**
 * 本机 sidecar：找系统 Python → 家目录 venv → pip 装本仓 → spawn。
 * 不走 shell。只绑 loopback。
 *
 * 设置页「停止」按配置的 loopback 端口停掉正在听的进程（含 keep-alive
 * 留下的、别的库拉起的、旧版残留）。退出 Obsidian 仍只杀本次 spawn 的
 * 子进程（保活默认开，多库共用）。
 */
import { execFile as execFileCb, spawn, type ChildProcess } from "child_process";
import { existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import { promisify } from "util";
import { makeApi } from "./api";
import { ApiError } from "./apierror";

const execFile = promisify(execFileCb);

export type SidecarPhase =
  | "idle"
  | "checking"
  | "installing"
  | "starting"
  | "stopping"
  | "running"
  | "stale"
  | "error";

export type SidecarSnap = {
  phase: SidecarPhase;
  detail: string;
  owned: boolean;
};

export type SidecarOpts = {
  sidecarUrl: string;
  pythonPath: string;
  version: string;
  autoStart: boolean;
};

export type EnsureKind = "already" | "started" | "stale" | "error";

export type StopWho = "owned" | "leftover" | "shared" | "other" | "";

export type StopResult = {
  kind: "stopped" | "idle" | "failed";
  who: StopWho;
  command: string;
};

export type ListenProc = { pid: number; command: string };

const HOME = join(homedir(), ".socrates-pen");
const VENV = join(HOME, "venv");

function venvPython(): string {
  const win = join(VENV, "Scripts", "python.exe");
  const nix = join(VENV, "bin", "python");
  return existsSync(win) ? win : nix;
}

function parseListen(url: string): { host: string; port: number } {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    throw new Error("bad-url");
  }
  const host = (u.hostname || "127.0.0.1").toLowerCase();
  if (host !== "127.0.0.1" && host !== "localhost" && host !== "::1") {
    throw new Error("not-loopback");
  }
  const port = u.port ? Number(u.port) : u.protocol === "https:" ? 443 : 80;
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("bad-port");
  return { host: host === "localhost" ? "127.0.0.1" : host, port };
}

function zipUrl(version: string): string {
  return `https://github.com/xesws/socrates-pen/archive/refs/tags/${version}.zip`;
}

export function cmpVer(a: string, b: string): number {
  const pa = a.split(".").map((x) => parseInt(x, 10) || 0);
  const pb = b.split(".").map((x) => parseInt(x, 10) || 0);
  for (let i = 0; i < 3; i++) {
    const d = (pa[i] || 0) - (pb[i] || 0);
    if (d) return d;
  }
  return 0;
}

/** ping 通不算能干活：health 必须带 version，且 sidecar ≥ 插件 manifest。 */
export function sidecarUsable(version: string | undefined, pluginVersion: string): boolean {
  return typeof version === "string" && version.length > 0 && cmpVer(version, pluginVersion) >= 0;
}

/** `lsof -Fpc`：每段 `pPID` / `cCOMMAND`。 */
export function parseLsofF(stdout: string): ListenProc[] {
  const out: ListenProc[] = [];
  let pid = 0;
  let command = "";
  const flush = (): void => {
    if (pid > 0) out.push({ pid, command: command || "?" });
    pid = 0;
    command = "";
  };
  for (const line of stdout.split(/\r?\n/)) {
    if (!line) continue;
    if (line.startsWith("p")) {
      flush();
      pid = parseInt(line.slice(1), 10) || 0;
    } else if (line.startsWith("c")) {
      command = line.slice(1).trim();
    }
  }
  flush();
  return out.filter((p) => p.pid > 0);
}

function addrPort(addr: string): number | null {
  const m = addr.match(/:(\d+)$/);
  return m ? parseInt(m[1], 10) : null;
}

/** `netstat -ano -p TCP` 的 LISTENING 行，最后一列是 PID。 */
export function parseNetstatAno(stdout: string, port: number): ListenProc[] {
  const out: ListenProc[] = [];
  const seen = new Set<number>();
  for (const line of stdout.split(/\r?\n/)) {
    if (!/LISTEN/i.test(line)) continue;
    const parts = line.trim().split(/\s+/);
    const local = parts[1] || "";
    if (addrPort(local) !== port) continue;
    const pid = parseInt(parts[parts.length - 1], 10);
    if (!Number.isInteger(pid) || pid <= 0 || seen.has(pid)) continue;
    seen.add(pid);
    out.push({ pid, command: "?" });
  }
  return out;
}

/** `ss -ltnp 'sport = :PORT'`。 */
export function parseSsLtnp(stdout: string): ListenProc[] {
  const out: ListenProc[] = [];
  const seen = new Set<number>();
  const cmdRe = /users:\(\("([^"]+)"/;
  for (const line of stdout.split(/\r?\n/)) {
    if (!/listen/i.test(line)) continue;
    const command = line.match(cmdRe)?.[1] || "?";
    const re = /pid=(\d+)/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(line))) {
      const pid = parseInt(m[1], 10);
      if (!Number.isInteger(pid) || pid <= 0 || seen.has(pid)) continue;
      seen.add(pid);
      out.push({ pid, command });
    }
  }
  return out;
}

export function classifyListener(command: string, owned: boolean): StopWho {
  if (owned) return "owned";
  if (/python|pen/i.test(command)) return "leftover";
  return command ? "other" : "leftover";
}

async function run(
  bin: string,
  args: string[],
  timeoutMs: number,
): Promise<{ stdout: string; stderr: string }> {
  const { stdout, stderr } = await execFile(bin, args, {
    timeout: timeoutMs,
    windowsHide: true,
    encoding: "utf8",
  });
  return { stdout: String(stdout || ""), stderr: String(stderr || "") };
}

async function python311(bin: string, prefix: string[] = []): Promise<boolean> {
  try {
    await run(bin, [...prefix, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"], 8000);
    return true;
  } catch {
    return false;
  }
}

async function findSystemPython(override: string): Promise<{ bin: string; prefix: string[] } | null> {
  const trimmed = override.trim();
  if (trimmed) {
    if (await python311(trimmed)) return { bin: trimmed, prefix: [] };
    return null;
  }
  const nix = ["python3", "python"];
  for (const bin of nix) {
    if (await python311(bin)) return { bin, prefix: [] };
  }
  if (process.platform === "win32" && (await python311("py", ["-3"]))) {
    return { bin: "py", prefix: ["-3"] };
  }
  return null;
}

async function penVersion(py: string): Promise<string | null> {
  try {
    const { stdout } = await run(py, ["-c", "import pen; print(pen.__version__)"], 8000);
    const v = stdout.trim();
    return v || null;
  } catch {
    return null;
  }
}

function abortMs(ms: number): AbortSignal {
  const c = new AbortController();
  setTimeout(() => c.abort(), ms);
  return c.signal;
}

async function fetchHealth(
  baseUrl: string,
  timeoutMs = 2000,
): Promise<{ status: string; version?: string } | null> {
  try {
    return await makeApi(baseUrl).health({ signal: abortMs(timeoutMs) });
  } catch {
    return null;
  }
}

async function ping(baseUrl: string): Promise<boolean> {
  return (await fetchHealth(baseUrl, 800)) !== null;
}

async function waitHealth(baseUrl: string, ms: number): Promise<boolean> {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await ping(baseUrl)) return true;
    await new Promise((r) => setTimeout(r, 300));
  }
  return ping(baseUrl);
}

async function waitGone(baseUrl: string, ms: number): Promise<boolean> {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (!(await ping(baseUrl))) return true;
    await new Promise((r) => setTimeout(r, 200));
  }
  return !(await ping(baseUrl));
}

async function findListeners(port: number): Promise<ListenProc[]> {
  if (process.platform === "win32") {
    try {
      const { stdout } = await run("netstat", ["-ano", "-p", "TCP"], 8000);
      return parseNetstatAno(stdout, port);
    } catch {
      return [];
    }
  }
  try {
    const { stdout } = await run("lsof", ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-Fpc"], 8000);
    return parseLsofF(stdout);
  } catch {
    try {
      const { stdout } = await run("ss", ["-ltnp", `sport = :${port}`], 8000);
      return parseSsLtnp(stdout);
    } catch {
      return [];
    }
  }
}

async function killPid(pid: number, hard: boolean): Promise<void> {
  if (!Number.isInteger(pid) || pid <= 0 || pid === process.pid) return;
  if (process.platform === "win32") {
    const args = hard ? ["/PID", String(pid), "/T", "/F"] : ["/PID", String(pid), "/T"];
    await run("taskkill", args, 8000).catch(() => {});
    return;
  }
  try {
    process.kill(pid, hard ? "SIGKILL" : "SIGTERM");
  } catch {
    /* already gone */
  }
}

export class SidecarManager {
  private child: ChildProcess | null = null;
  private owned = false;
  private phase: SidecarPhase = "idle";
  private detail = "";
  private errTail = "";
  private watchers = new Set<() => void>();
  private inflight: Promise<EnsureKind> | null = null;
  private stopInflight: Promise<StopResult> | null = null;
  private epoch = 0;

  snapshot(): SidecarSnap {
    return { phase: this.phase, detail: this.detail, owned: this.owned };
  }

  watch(fn: () => void): () => void {
    this.watchers.add(fn);
    return () => this.watchers.delete(fn);
  }

  private set(phase: SidecarPhase, detail: string): void {
    this.phase = phase;
    this.detail = detail;
    for (const fn of this.watchers) fn();
  }

  /** 健康且版本对齐就直接过。旧进程占端口 → stale，不标 Running。 */
  ensure(opts: SidecarOpts): Promise<EnsureKind> {
    if (this.inflight) return this.inflight;
    this.inflight = this.runEnsure(opts).finally(() => {
      this.inflight = null;
    });
    return this.inflight;
  }

  /** 退出 Obsidian / 关保活：只杀本次 spawn 的子进程，不动别人占着的端口。 */
  stopOwned(): void {
    this.killChild();
    if (this.phase !== "stopping") this.set("idle", "");
  }

  /** 设置页「停止」：按配置的 loopback 端口停掉正在听的进程。 */
  stopListen(sidecarUrl: string): Promise<StopResult> {
    if (this.stopInflight) return this.stopInflight;
    this.stopInflight = this.runStop(sidecarUrl).finally(() => {
      this.stopInflight = null;
    });
    return this.stopInflight;
  }

  private killChild(): void {
    if (!this.child) {
      this.owned = false;
      return;
    }
    const kid = this.child;
    this.owned = false;
    this.child = null;
    try {
      kid.kill("SIGTERM");
    } catch {
      /* already gone */
    }
  }

  private async runEnsure(opts: SidecarOpts): Promise<EnsureKind> {
    this.epoch += 1;
    const my = this.epoch;
    if (this.stopInflight) await this.stopInflight;
    if (my !== this.epoch) return "error";

    this.errTail = "";
    this.set("checking", "");
    const health = await fetchHealth(opts.sidecarUrl);
    if (my !== this.epoch) return "error";
    if (health) {
      if (sidecarUsable(health.version, opts.version)) {
        this.set("running", health.version || "");
        return "already";
      }
      this.set("stale", health.version || "");
      return "stale";
    }
    let listen: { host: string; port: number };
    try {
      listen = parseListen(opts.sidecarUrl);
    } catch (e) {
      const code = e instanceof Error ? e.message : "bad-url";
      this.set("error", code);
      return "error";
    }
    const sys = await findSystemPython(opts.pythonPath);
    if (my !== this.epoch) return "error";
    if (!sys) {
      this.set("error", "no-python");
      return "error";
    }
    const vpy = venvPython();
    const haveVenv = existsSync(vpy);
    const ver = haveVenv ? await penVersion(vpy) : null;
    const needInstall = !haveVenv || !ver || cmpVer(ver, opts.version) < 0;
    if (needInstall) {
      this.set("installing", "");
      try {
        if (!haveVenv) {
          await run(sys.bin, [...sys.prefix, "-m", "venv", VENV], 120000);
        }
        const py = venvPython();
        if (!existsSync(py)) throw new Error("venv-missing");
        await run(py, ["-m", "pip", "install", "--upgrade", "pip"], 180000);
        try {
          await run(py, ["-m", "pip", "install", zipUrl(opts.version)], 300000);
        } catch {
          await run(py, ["-m", "pip", "install", "git+https://github.com/xesws/socrates-pen.git"], 300000);
        }
      } catch (e) {
        if (my !== this.epoch) return "error";
        const msg = e instanceof Error ? e.message : String(e);
        this.errTail = msg.slice(-800);
        this.set("error", "install-failed");
        return "error";
      }
    }
    if (my !== this.epoch) return "error";
    this.set("starting", "");
    const py = venvPython();
    try {
      const child = spawn(py, ["-m", "pen", "--host", listen.host, "--port", String(listen.port)], {
        cwd: HOME,
        env: { ...process.env, PYTHONUNBUFFERED: "1" },
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      });
      this.child = child;
      this.owned = true;
      const bits: string[] = [];
      const onChunk = (buf: Buffer) => {
        bits.push(buf.toString("utf8"));
        if (bits.join("").length > 4000) bits.splice(0, bits.length - 4);
      };
      child.stdout?.on("data", onChunk);
      child.stderr?.on("data", onChunk);
      child.on("error", (err) => {
        this.errTail = err.message;
        if (this.child === child) {
          this.owned = false;
          this.child = null;
          this.set("error", "spawn-failed");
        }
      });
      child.on("exit", (code) => {
        if (this.child !== child) return;
        this.owned = false;
        this.child = null;
        this.errTail = bits.join("").slice(-800);
        if (this.phase !== "running") this.set("error", `exited-${code ?? "?"}`);
        else this.set("idle", "");
      });
      const ok = await waitHealth(opts.sidecarUrl, 15000);
      if (my !== this.epoch) {
        this.killChild();
        return "error";
      }
      if (!ok) {
        this.errTail = bits.join("").slice(-800);
        this.killChild();
        this.set("error", "no-health");
        return "error";
      }
      const after = await fetchHealth(opts.sidecarUrl);
      if (my !== this.epoch) {
        this.killChild();
        return "error";
      }
      if (!sidecarUsable(after?.version, opts.version)) {
        this.set("stale", after?.version || "");
        return "stale";
      }
      this.set("running", after?.version || opts.version);
      return "started";
    } catch (e) {
      this.errTail = e instanceof Error ? e.message : String(e);
      this.set("error", "spawn-failed");
      return "error";
    }
  }

  private async runStop(sidecarUrl: string): Promise<StopResult> {
    this.epoch += 1;
    const my = this.epoch;
    this.errTail = "";

    let listen: { host: string; port: number };
    try {
      listen = parseListen(sidecarUrl);
    } catch (e) {
      const code = e instanceof Error ? e.message : "bad-url";
      this.set("error", code);
      return { kind: "failed", who: "", command: "" };
    }

    const wasOwned = this.owned;
    const prior = this.phase;
    const health = await fetchHealth(sidecarUrl);
    if (!health && !this.child) {
      const hanging = await findListeners(listen.port);
      if (!hanging.length) {
        if (my === this.epoch) this.set("idle", "");
        return { kind: "idle", who: "", command: "" };
      }
    }

    this.set("stopping", "");
    this.killChild();

    const whoFromPrior = (): StopWho => {
      if (wasOwned) return "owned";
      if (prior === "stale" || (health && !health.version)) return "leftover";
      if (prior === "running") return "shared";
      return "leftover";
    };

    let shutdownAccepted = false;
    try {
      await makeApi(sidecarUrl).shutdown({ signal: abortMs(2000) });
      shutdownAccepted = true;
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      if (status !== 404 && status !== 405) {
        /* 超时或连不上：下面改杀占用端口的进程 */
      }
    }
    if (shutdownAccepted && (await waitGone(sidecarUrl, 2500))) {
      const who = whoFromPrior();
      if (my === this.epoch) this.set("idle", "stopped");
      return { kind: "stopped", who, command: who === "owned" ? "" : "python" };
    }

    const procs = await findListeners(listen.port);
    const command = procs.map((p) => p.command).filter((c) => c && c !== "?")[0] || "";
    const who = wasOwned || prior === "stale" || prior === "running"
      ? whoFromPrior()
      : classifyListener(command, false);
    for (const p of procs) await killPid(p.pid, false);
    if (await waitGone(sidecarUrl, 2500)) {
      if (my === this.epoch) this.set("idle", "stopped");
      return { kind: "stopped", who, command };
    }
    for (const p of procs) await killPid(p.pid, true);
    if (await waitGone(sidecarUrl, 2000)) {
      if (my === this.epoch) this.set("idle", "stopped");
      return { kind: "stopped", who, command };
    }

    if (my === this.epoch) {
      this.errTail = procs.length ? command || String(procs[0].pid) : "no-pid";
      this.set("error", procs.length ? "stop-failed" : "stop-no-pid");
    }
    return { kind: "failed", who, command };
  }

  lastError(): string {
    return this.errTail;
  }
}
