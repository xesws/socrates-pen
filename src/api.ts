import { ApiError } from "./apierror";
import { currentLang } from "./i18n";
import type { PenSettings } from "./settings";
import { limitsPayload, llmPayload } from "./settings";
import type {
  DeepInbox,
  HandbookMeta,
  LlmStatus,
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
    health: () => j<{ status: string; llm: LlmStatus }>(baseUrl, "/v1/health"),
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
