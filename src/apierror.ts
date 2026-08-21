/**
 * 带 HTTP 状态码的错误。**这个文件不许 import 任何东西。**
 *
 * 为什么必须有：调用方要区分「会话没了」「笔记被改名」「sidecar 连不上」，
 * 而裸 `Error` 里只有一句**本地化**的服务端文案（「会话不存在」/
 * "unknown session"）——`deeppoll.ts` 里那句 `message.includes("404")`
 * 因此永远为假，一条写了、看着有、其实早就死了的终止条件。
 * 状态码分得开第三种，`code` 分得开前两种（见下面 `isGone`）。
 *
 * 为什么单独一个文件而不是塞进 `api.ts`：`deeppoll.ts` 要用它。而 `api.ts`
 * 依赖 `settings.ts` → `obsidian`，deeppoll 一旦顺着这条链走，它就不再是叶子，
 * `check-poll.mjs`（platform: neutral，不 external obsidian）当场打不动包。
 * 独立成零依赖的叶子，两边都能安全引。
 */
export class ApiError extends Error {
  readonly status: number;
  /** 服务端给的机器可读错误码，没有就是空串。见 `isGone` 下面那段。 */
  readonly code: string;

  constructor(status: number, message: string, code = "") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/**
 * 这个错是不是**「这场会话没了」**。会话被保留期清理掉之后走的就是它。
 *
 * **为什么光看 404 不够**：`_meta_or_404`（`pen/app.py`）对「笔记被改名或
 * 移走」也抛 404——那是读者在 Obsidian 里的日常操作，而那条 detail 里有唯一
 * 能救他的一句「请重新框选一次」。只看状态码的话，这句正确指引会被换成
 * 「这场对话已归档」+「新会话没开起来」，两句都不是真的。
 * 所以认服务端明写的 `session_gone`，不认状态码。
 */
export function isGone(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404 && e.code === "session_gone";
}
