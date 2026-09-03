import { ApiError } from "./apierror";
import { currentLang } from "./i18n";
import type { chipPayload } from "./customchips";
import type { PenSettings } from "./settings";
import { limitsPayload, llmPayload } from "./settings";
import type {
  DeepInbox,
  HandbookMeta,
  Health,
  LlmStatus,
  PreflightReport,
  ProfileCodeResult,
  ProfileList,
  ProfileView,
  SessionView,
  SnapshotStatus,
  UsageTotal,
} from "./types";

export { ApiError, isGone } from "./apierror";

function joinUrl(base: string, path: string): string {
  return `${base.replace(/\/$/, "")}${path}`;
}

async function j<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(joinUrl(base, path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      // sidecar 的错误文案按这个头选语言。走 header 而不是 body 字段，
      // 是为了让没有 body 的 GET 路由也能覆盖。
      "Accept-Language": currentLang(),
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json() as Promise<T>;
}

/**
 * 把 sidecar 的错误 body 解析成 `ApiError`。**两处出错的地方（`j` 和
 * `readSse`）必须同源**，否则又是一次「两个闸不同源」。
 *
 * `detail` 有两种形状：普通错误是一句本地化文案（字符串），而「这场会话没了」
 * 是 `{code, message}`。带上 code 是因为**光看 404 分不清**是会话被清理了
 * 还是读者把笔记改名／移走了——细节见 `apierror.ts` 里 `isGone` 的注释。
 * 走 body 而不是自定义响应头：响应头要 CORS `expose_headers` 才读得到，
 * 而 detail 这条路本来就在解析。
 */
async function errorFrom(res: Response): Promise<ApiError> {
  let message = res.statusText;
  let code = "";
  try {
    const body = (await res.json()) as { detail?: unknown };
    const d = body?.detail;
    if (d && typeof d === "object") {
      const o = d as { code?: unknown; message?: unknown };
      if (typeof o.code === "string") code = o.code;
      message = typeof o.message === "string" && o.message ? o.message : JSON.stringify(body);
    } else {
      message = typeof d === "string" && d ? d : JSON.stringify(body);
    }
  } catch {
    /* 没 body 或不是 JSON：留着 statusText */
  }
  return new ApiError(res.status, message, code);
}

/** 跨会话累计。设置页那块统计用它——不走 makeApi 是因为设置页拿不到 view。 */
export async function usageTotal(baseUrl: string): Promise<UsageTotal> {
  return j<UsageTotal>(baseUrl, "/v1/usage");
}

/** 插件启动时打一枪，让 sidecar 清掉过期会话。fire-and-forget。 */
export async function purgeExpired(baseUrl: string): Promise<void> {
  await j<{ scanned: number; removed: number }>(baseUrl, "/v1/maintenance/purge", {
    method: "POST",
  });
}

export function makeApi(baseUrl: string) {
  return {
    health: (init?: RequestInit) => j<Health>(baseUrl, "/v1/health", init),
    /** 新 sidecar 的优雅退出。旧版 404/405，调用方改杀占用端口的进程。 */
    shutdown: (init?: RequestInit) =>
      j<{ status: string }>(baseUrl, "/v1/shutdown", { method: "POST", ...init }),
    /** v0.18.0：钥匙的只写入口。落 sidecar 家目录（0600），vault 拿不到全文。 */
    putLlmKey: (api_key: string, base_url?: string) =>
      j<LlmStatus>(baseUrl, "/v1/llm/key", {
        method: "PUT",
        body: JSON.stringify({ api_key, base_url: base_url || "" }),
      }),
    deleteLlmKey: () =>
      j<LlmStatus>(baseUrl, "/v1/llm/key", { method: "DELETE" }),
    /** 快模型的钥匙。**必须是独立通道**：它在另一台主机上，而后端那条
     *  跨主机保护见到「换了主机又没自带 key」会直接判成没配置。 */
    putFastKey: (api_key: string, base_url?: string) =>
      j<LlmStatus>(baseUrl, "/v1/llm/fast-key", {
        method: "PUT",
        body: JSON.stringify({ api_key, base_url: base_url || "" }),
      }),
    deleteFastKey: () =>
      j<LlmStatus>(baseUrl, "/v1/llm/fast-key", { method: "DELETE" }),
    /**
     * 配置体检：**让 sidecar 真往节点打一枪**，回答「这套设置现在能不能用」。
     *
     * health 回答不了这个——它只知道槽里有没有钥匙。钥匙是废的、model 在
     * 这个节点上不存在、节点没有视觉，它一概显示正常（v0.22.2 读者报告）。
     *
     * 发的是 `llmPayload(settings)`，和 /v1/chat 逐字同源：体检别的一套
     * 配置，等于没体检。**调用方必须自己节流**——这是一次真实的 API 调用，
     * 只该在配置变了的时候跑，不能进轮询。
     */
    preflight: (settings: PenSettings, fast?: boolean) =>
      j<PreflightReport>(baseUrl, "/v1/llm/preflight", {
        method: "POST",
        body: JSON.stringify({ ...llmPayload(settings), ...(fast ? { fast: true } : {}) }),
      }),
    importHandbook: (original_path: string, handbook_id: string, vault_root?: string) =>
      j<HandbookMeta>(baseUrl, "/v1/handbooks/import", {
        method: "POST",
        body: JSON.stringify({ original_path, handbook_id, vault_root }),
      }),
    createSession: (handbook_id: string, session_id?: string) =>
      j<SessionView>(baseUrl, "/v1/sessions", {
        method: "POST",
        body: JSON.stringify({ handbook_id, session_id }),
      }),
    getSession: (session_id: string) =>
      j<SessionView>(baseUrl, `/v1/sessions/${session_id}`),
    compactSession: (session_id: string) =>
      j<SessionView & { did?: boolean; dropped_reads?: number }>(
        baseUrl,
        `/v1/sessions/${session_id}/compact`,
        { method: "POST" },
      ),
    deepInbox: (session_id: string, since: number) =>
      j<DeepInbox>(baseUrl, `/v1/sessions/${session_id}/deep?since=${since}`),
    snapshots: (handbook_id: string) =>
      j<SnapshotStatus>(baseUrl, `/v1/handbooks/${handbook_id}/snapshots`),
    rollback: (handbook_id: string) =>
      j<SnapshotStatus & { ok: boolean; restored_from: string; original_path: string }>(baseUrl, "/v1/writeback/rollback", {
        method: "POST",
        body: JSON.stringify({ handbook_id }),
      }),
    redo: (handbook_id: string) =>
      j<SnapshotStatus & { ok: boolean; restored_from: string; original_path: string }>(baseUrl, "/v1/writeback/redo", {
        method: "POST",
        body: JSON.stringify({ handbook_id }),
      }),
    /**
     * v0.25.0 学习画像：编下一批轮次。**一律主模型**——body 与 /v1/chat 同源
     * （`llmPayload`），所以永远不含 api_key（check-key.mjs / check-api.mjs 守着）。
     * `force` 只在真时出现：重算是读者两步确认过的动作，老路请求体一个键不多。
     * 每次是一枪真实的 API 调用，调用方（ReportView）自带停滞保护。
     */
    codeProfile: (
      handbook_id: string,
      settings: PenSettings,
      opts?: { force?: boolean; maxBatches?: number; signal?: AbortSignal },
    ) => {
      const lim = limitsPayload(settings);
      return j<ProfileCodeResult>(baseUrl, `/v1/handbooks/${handbook_id}/profile/code`, {
        method: "POST",
        ...(opts?.signal ? { signal: opts.signal } : {}),
        body: JSON.stringify({
          ...llmPayload(settings),
          ...(lim ? { limits: lim } : {}),
          max_batches: opts?.maxBatches ?? 3,
          ...(opts?.force ? { force: true } : {}),
        }),
      });
    },
    getProfile: (handbook_id: string, signal?: AbortSignal) =>
      j<ProfileView>(baseUrl, `/v1/handbooks/${handbook_id}/profile`, signal ? { signal } : undefined),
    /** 这个库的书架。`vault_root` 是绝对路径，带空格和中文，必须编码。 */
    listProfiles: (vault_root: string, signal?: AbortSignal) =>
      j<ProfileList>(
        baseUrl,
        `/v1/profiles?vault_root=${encodeURIComponent(vault_root)}`,
        signal ? { signal } : undefined,
      ),
  };
}

export async function streamChat(
  baseUrl: string,
  body: {
    session_id: string;
    selected_text: string;
    start_line: number;
    end_line: number;
    chip: string;
    user_text: string;
    deep?: boolean;
    images?: { mime: string; data: string }[];
    // 自定义泡泡随请求上行：它们住在这个 vault 的 data.json，后端一个字都不存。
    // **必须是可选的**——scripts/check-api.mjs:115 用一个不含 deep/images 的
    // 裸对象调这个函数，设成必填会让 `tsc --noEmit` 当场红。
    // 形状与 chipPayload()（src/customchips.ts）同源，别在这儿另写一份。
    custom_chip?: ReturnType<typeof chipPayload>;
  },
  onEvent: (ev: Record<string, unknown>) => void,
  settings?: PenSettings,
): Promise<void> {
  // 算一次存下来，别在下面调两遍。
  const lim = settings ? limitsPayload(settings) : undefined;
  const res = await fetch(joinUrl(baseUrl, "/v1/chat"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept-Language": currentLang(),
    },
    body: JSON.stringify({
      ...body,
      ...(settings ? llmPayload(settings) : {}),
      // 只发改过的那几个；一个都没动时 limitsPayload 返回 undefined，
      // 请求体里连 limits 这个键都不出现——「上线当天逐字节一致」就是这么来的。
      ...(lim ? { limits: lim } : {}),
      // Fast Mode 的开关位。**从 settings 取，不从 body 取**：开关写的就是
      // settings.fastMode，从 body 走等于让每个调用点各记一遍同一个状态。
      // 关着时这个键压根不出现，老路逐字节一致。
      //
      // streamApprove 那边**故意没有这一段**：点了允许的那半轮必然执行
      // edit_file，让它跑在写不了盘的模型上没有意义。
      ...(settings?.fastMode === true ? { fast: true } : {}),
    }),
  });
  await readSse(res, onEvent);
}

export async function streamApprove(
  baseUrl: string,
  body: { session_id: string; pending_id: string; allow: boolean },
  onEvent: (ev: Record<string, unknown>) => void,
  settings?: PenSettings,
): Promise<void> {
  const lim = settings ? limitsPayload(settings) : undefined;
  const res = await fetch(joinUrl(baseUrl, "/v1/chat/approve"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept-Language": currentLang(),
    },
    body: JSON.stringify({
      ...body,
      ...(settings ? llmPayload(settings) : {}),
      // 只发改过的那几个；一个都没动时 limitsPayload 返回 undefined，
      // 请求体里连 limits 这个键都不出现——「上线当天逐字节一致」就是这么来的。
      ...(lim ? { limits: lim } : {}),
    }),
  });
  await readSse(res, onEvent);
}

async function readSse(
  res: Response,
  onEvent: (ev: Record<string, unknown>) => void,
): Promise<void> {
  if (!res.ok || !res.body) throw await errorFrom(res);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  const takeFrames = (chunk: string): string => {
    const parts = chunk.split("\n\n");
    const rest = parts.pop() || "";
    for (const part of parts) {
      const line = part
        .split("\n")
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6))
        .join("");
      if (!line) continue;
      onEvent(JSON.parse(line) as Record<string, unknown>);
    }
    return rest;
  };
  while (true) {
    const { done, value } = await reader.read();
    if (value) buf += dec.decode(value, { stream: true });
    if (done) {
      buf += dec.decode();
      if (buf && !buf.endsWith("\n\n")) buf += "\n\n";
      takeFrames(buf);
      break;
    }
    buf = takeFrames(buf);
  }
}
