/**
 * 本机 sidecar：找系统 Python → 家目录 venv → pip 装本仓 → spawn。
 * 不走 shell。只绑 loopback。不是我们拉起的进程，停止按钮不会去杀。
 */
import { execFile as execFileCb, spawn, type ChildProcess } from "child_process";
import { existsSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import { promisify } from "util";
import { makeApi } from "./api";

const execFile = promisify(execFileCb);

export type SidecarPhase =
  | "idle"
  | "checking"
  | "installing"
  | "starting"
  | "running"
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

function cmpVer(a: string, b: string): number {
  const pa = a.split(".").map((x) => parseInt(x, 10) || 0);
  const pb = b.split(".").map((x) => parseInt(x, 10) || 0);
  for (let i = 0; i < 3; i++) {
    const d = (pa[i] || 0) - (pb[i] || 0);
    if (d) return d;
  }
  return 0;
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

async function ping(baseUrl: string): Promise<boolean> {
  try {
    await makeApi(baseUrl).health();
    return true;
  } catch {
    return false;
  }
}

async function waitHealth(baseUrl: string, ms: number): Promise<boolean> {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (await ping(baseUrl)) return true;
    await new Promise((r) => setTimeout(r, 300));
  }
  return ping(baseUrl);
}

export class SidecarManager {
  private child: ChildProcess | null = null;
  private owned = false;
  private phase: SidecarPhase = "idle";
  private detail = "";
  private errTail = "";
  private watchers = new Set<() => void>();
  private inflight: Promise<void> | null = null;

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

  /** 健康就直接过。否则安装（如需）并拉起我们自己的进程。 */
  ensure(opts: SidecarOpts): Promise<void> {
    if (this.inflight) return this.inflight;
    this.inflight = this.runEnsure(opts).finally(() => {
      this.inflight = null;
    });
    return this.inflight;
  }

  stopOwned(): void {
    if (!this.owned || !this.child) return;
    const kid = this.child;
    this.owned = false;
    this.child = null;
    try {
      kid.kill("SIGTERM");
    } catch {
      /* already gone */
    }
    this.set("idle", "");
  }

  private async runEnsure(opts: SidecarOpts): Promise<void> {
    this.errTail = "";
    this.set("checking", "");
    if (await ping(opts.sidecarUrl)) {
      this.set("running", "");
      return;
    }
    let listen: { host: string; port: number };
    try {
      listen = parseListen(opts.sidecarUrl);
    } catch (e) {
      const code = e instanceof Error ? e.message : "bad-url";
      this.set("error", code);
      return;
    }
    const sys = await findSystemPython(opts.pythonPath);
    if (!sys) {
      this.set("error", "no-python");
      return;
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
        const msg = e instanceof Error ? e.message : String(e);
        this.errTail = msg.slice(-800);
        this.set("error", "install-failed");
        return;
      }
    }
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
      if (!ok) {
        this.errTail = bits.join("").slice(-800);
        this.stopOwned();
        this.set("error", "no-health");
        return;
      }
      this.set("running", "");
    } catch (e) {
      this.errTail = e instanceof Error ? e.message : String(e);
      this.set("error", "spawn-failed");
    }
  }

  lastError(): string {
    return this.errTail;
  }
}
