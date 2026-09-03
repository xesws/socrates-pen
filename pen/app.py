"""FastAPI：阅读原文、就地问、确认后原地写回 original_path。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pen import __version__
from pen import gitops
from pen import library_scan
from pen import insert as insertmod
from pen.outline import file_outline
from pen import libraries, snapshots
from pen import diagnose as diagnosemod
from pen import profile as profilemod
from pen import proposals as proposalsmod
from pen import probe as probemod, probe_store, retention, trajectory
from pen import config as configmod
from pen import meter as metermod
from pen.clock import now_iso
from pen.config import DEFAULT_HANDBOOK_ID, LLMConfig, llm_public_status, merge_llm
from pen.i18n import localized, msg, norm_lang
from pen.libraries import RegisterError
from pen.sandbox import SandboxError, assert_handbook_path, parse_vault_root, reading_roots
from pen.chips import CUSTOM_ID_RE, CustomChipSpec, normalize_custom_chip
from pen.routing import route_for
from pen.session import FIXED_CHIPS, STORE, apply_session_lang, chip_label
from pen.compact import (
    CompactPending,
    allow_paths_for,
    compact_session,
    should_auto_compact,
)
from pen.tutor import (
    ProviderError,
    build_user_packet,
    propose_fold_md,
    read_roots,
    resume_chat,
    stream_chat,
)
from pen import vision as visionmod
from pen import preflight
from pen import tutor as tutormod

SEARCH_REPLY = (
    "论文检索还没开。这是诚实挂起：P2 才有联网，"
    "现在不会假装搜过，也不会往诊断轨迹里记一笔假检索。"
)

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    libraries.ensure_default()
    # 起进程就扫一遍过期会话。uvicorn 那边 reload=False，所以这里只跑一次。
    # purge 保证不抛——目录不存在、文件被并发删都不该让 sidecar 起不来。
    retention.purge_expired_sessions()
    yield


# 版本号**从 `pen/__init__.py` 读**，不在这儿抄一份字面量。抄的那份会漂：
# 它一直卡在 0.12.13，而 manifest.json / package.json / pyproject.toml 三家
# 早就是 0.13.1 了。全仓没有任何代码读这个字段（唯一出口是 /openapi.json 的
# info.version），所以它一连落下两个发布都没人发现——正是「没人读的常量必然过期」。
app = FastAPI(title="Socratic Pen", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
        "app://obsidian.md",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_proposals: dict[str, dict[str, Any]] = {}


class LlmOverrideBody(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    thinking: str | None = None
    vision: bool | None = None
    # 嵌套一个对象，不平铺十几个键：平铺会让 ChatBody 变成二十来个字段。
    # 类型写 dict[str, Any] 而不是子模型，是为了**永远不会 422**——设置页填了
    # 个字符串，读者该看到夹紧后的正常回复，不是一个红色 422。
    # merge_limits 只夹紧不报错，看不懂的当没给。
    limits: dict[str, Any] | None = None
    # 快模型的端点与型号。**没有 fast_api_key**——密钥只走 PUT /v1/llm/fast-key，
    # 永不随请求体上行（scripts/check-key.mjs 机械守着这条）。
    fast_base_url: str | None = None
    fast_model: str | None = None
    # 读者显式选的厂商。**只影响推理档怎么拼**（pen/providers.py），
    # 缺省 / "auto" 就按型号名猜，也就是 v0.23.0 之前的行为。
    provider: str | None = None
    fast_provider: str | None = None

    def merged_limits(self) -> configmod.RuntimeLimits:
        return configmod.merge_limits(self.limits)

    def fast_status(self) -> tuple[LLMConfig | None, str]:
        """快模型这一路的 cfg **和它没配成的理由**。配成了理由是空串。"""
        return configmod.fast_llm_status(
            base_url=self.fast_base_url,
            model=self.fast_model,
            thinking=self.thinking,
            vision=self.vision,
            provider=self.fast_provider,
        )

    def merged_fast(self) -> LLMConfig | None:
        """只要 cfg 不要理由。实现在 fast_status，这里不另算一遍。"""
        return self.fast_status()[0]

    def merged(self) -> LLMConfig | None:
        return merge_llm(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            thinking=self.thinking,
            vision=self.vision,
            provider=self.provider,
        )


class ManagedKeyBody(BaseModel):
    """设置页的只写入口。key 只落 PEN_DIR/llm.json（0600），任何 GET 都不回全文。"""

    api_key: str
    base_url: str | None = None


class ImportBody(BaseModel):
    original_path: str
    handbook_id: str | None = None
    vault_root: str | None = None


class SessionBody(BaseModel):
    handbook_id: str = DEFAULT_HANDBOOK_ID
    session_id: str | None = None


class ApproveBody(LlmOverrideBody):
    session_id: str
    pending_id: str
    allow: bool


class ChatBody(LlmOverrideBody):
    session_id: str
    selected_text: str
    start_line: int
    end_line: int
    chip: str = "socratic"
    user_text: str = ""
    # 设置页的深挖开关。关掉时后端也不起线程——只断前端轮询的话，
    # 后台照样在烧钱，读者却看不到任何东西。
    deep: bool = True
    # 对话框贴的图。data 是无前缀的 base64。闸在 normalize_images。
    images: list[Any] | None = None
    # 读者自定义的芯片，随请求上行——它们住在 vault 的 data.json 里，后端不存。
    # 嵌套一个对象而不是平铺 id/label/prompt/writeback 四个键，对齐上面 limits
    # 那条先例；下一版接格式校验时线上形状不用再变。
    #
    # 类型写 dict[str, Any] 而不是子模型，理由同 limits 和 images：**值**一律不校验。
    # 读者在设置页写的那些字全都落在值上（label / prompt / writeback），
    # 所以「读者写了个奇怪的东西」→ 夹紧后的正常回复，夹在 normalize_custom_chip。
    #
    # 但要把话说准：dict[...] 挡的是**容器**。真发上来一个字符串或数字，
    # pydantic 会在进函数之前就 422。那一格不是读者写得出来的——前端这个字段
    # 恒由 chipPayload() 拼（src/customchips.ts），只有客户端坏了才发得出，
    # 和 images 收到 "oops" 是同一种 422，让它响是对的。
    custom_chip: dict[str, Any] | None = None
    # 侧栏顶栏那个 Fast Mode 开关。默认 False：不开时请求体里连这个键都不出现，
    # 老路逐字节一致。真正走不走快模型由 routing.route_for 定，这只是「读者
    # 允许了」——没配快模型的钥匙时它会被 no-fast-key 挡回基座，不报错。
    fast: bool = False


class ProposeBody(LlmOverrideBody):
    session_id: str
    summary_hint: str | None = None


class RetargetBody(BaseModel):
    proposal_id: str
    kind: str = "auto"
    after_line: int | None = None
    heading_start_line: int | None = None
    q_start_line: int | None = None
    range_start: int | None = None
    range_end: int | None = None


class ApplyBody(BaseModel):
    session_id: str
    proposal_id: str
    commit: bool = False
    commit_message: str | None = None


class RollbackBody(BaseModel):
    handbook_id: str


def req_lang(accept_language: str | None = Header(default=None)) -> str:
    """请求语言。走 Accept-Language 头而不是 body 字段——GET 路由也能覆盖，
    而且不用给七个 Pydantic 模型各加一遍。"""
    return norm_lang(accept_language)


def _meta_or_404(handbook_id: str, lang: str = "zh"):
    meta = libraries.get(handbook_id)
    if meta is None:
        raise HTTPException(404, msg("handbook.unknown", lang, handbook_id=handbook_id))
    try:
        return libraries.refresh_if_stale(handbook_id)
    except FileNotFoundError as exc:
        # 用户在 Obsidian 里重命名或移走了已登记的笔记。以前这里没人接，
        # 直接冒成 500 Internal Server Error。
        raise HTTPException(404, localized(exc, lang)) from exc


def _no_session(lang: str) -> HTTPException:
    """「这场会话没了」。**detail 带机器可读的 code。**

    为什么不能让前端只看 404：`_meta_or_404` 对「笔记被改名或移走」也抛 404，
    而那条 detail 里有唯一能救读者的一句「请重新框选一次」。前端把任意 404
    都当成「会话已归档」的话，正确指引就被吞掉，换成一句假因由
    （「已归档，而且新会话没开起来」——两句都不是真的）。

    走 body 里的 code 而不是响应头：自定义响应头要 CORS `expose_headers` 才读得到，
    而 detail 这条路 `j()` 本来就在解析，一个地方改完所有调用方都认。
    """
    return HTTPException(404, {"code": "session_gone", "message": msg("session.unknown", lang)})


def _sse(ev: dict[str, Any]) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


def _try_lock_session(sess, lang: str = "zh"):
    """抢会话锁。**返回 `(锁, 当家实例)`——调用方必须改用还回来的那个 sess。**

    走 STORE.try_lock 而不是 lock_for + acquire：那两步之间有一条缝，
    内存淘汰正好挤进来就会把 _locks[sid] 换掉，两个线程各拿一把不同的锁
    同时进临界区。抢锁必须和淘汰互斥，所以它整个发生在 store 的 _meta 里。

    还回来的 sess 可能**不是**传进去的那个：`get()` 到这里之间隔着
    `_meta_or_404` / `load_index` 几毫秒的磁盘 I/O，手上那个可能已经被淘汰
    扫走、别人跑完一整轮又写回盘上了。拿旧的接着跑 = 收尾 save 把别人
    那一轮整个盖掉。详见 `SessionStore.try_lock` 的注释。
    """
    got = STORE.try_lock(sess)
    if got is None:
        raise HTTPException(409, msg("session.busy", lang))
    return got


def _content_fp(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def _span(path: Path, start: int, end: int) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[max(0, start - 1) : end])


def _public_proposal(pid: str, path: Path, plan: insertmod.InsertPlan, diff: str) -> dict[str, Any]:
    return {
        "proposal_id": pid,
        "original_path": str(path),
        "mode": plan.mode,
        "level": plan.level,
        "q_title": plan.q_title,
        "beat": plan.beat,
        "instance_n": plan.instance_n,
        "insert_after_line": plan.insert_after_line,
        "replace_start": plan.replace_start,
        "replace_end": plan.replace_end,
        "fold_md": plan.fold_md,
        "diff": diff,
        "where": insertmod.describe_plan(plan),
    }


def _plan_for_target(
    path: Path,
    fold_md: str,
    sess,
    body: RetargetBody,
) -> insertmod.InsertPlan:
    kind = (body.kind or "auto").strip()
    if kind == "auto":
        if not sess.last_anchor:
            raise insertmod.InsertError("缺少框选锚点")
        idx = libraries.load_index(sess.handbook_id)
        return insertmod.plan_insert(
            idx,
            path,
            line=int(sess.last_anchor["start_line"]),
            fold_md=fold_md,
        )
    if kind == "after_line":
        if body.after_line is None:
            raise insertmod.InsertError("需要 after_line")
        return insertmod.plan_after_line(path, fold_md, int(body.after_line))
    ol = file_outline(path)
    if kind == "after_heading":
        hit = next(
            (h for h in ol["headings"] if h["start_line"] == body.heading_start_line),
            None,
        )
        if hit is None:
            raise insertmod.InsertError("找不到该标题")
        span = _span(path, int(hit["start_line"]), int(hit["end_line"]))
        return insertmod.plan_after_line(
            path,
            fold_md,
            int(hit["end_line"]),
            mode="after_heading",
            beat=str(hit["text"]),
            count_in=span,
        )
    if kind == "after_q":
        hit = next(
            (q for q in ol["questions"] if q["start_line"] == body.q_start_line),
            None,
        )
        if hit is None:
            raise insertmod.InsertError("找不到该问")
        span = _span(path, int(hit["start_line"]), int(hit["end_line"]))
        return insertmod.plan_after_line(
            path,
            fold_md,
            int(hit["insert_after_line"]),
            mode="after_q",
            q_title=str(hit["text"]),
            count_in=span,
        )
    if kind == "replace_heading":
        hit = next(
            (h for h in ol["headings"] if h["start_line"] == body.heading_start_line),
            None,
        )
        if hit is None:
            raise insertmod.InsertError("找不到该标题")
        start, end = int(hit["start_line"]), int(hit["end_line"])
        if end <= start:
            return insertmod.plan_after_line(
                path, fold_md, start, mode="after_heading", beat=str(hit["text"])
            )
        return insertmod.plan_replace_range(
            path,
            fold_md,
            start + 1,
            end,
            mode="replace_heading",
            beat=str(hit["text"]),
        )
    if kind == "replace_range":
        if body.range_start is None or body.range_end is None:
            raise insertmod.InsertError("需要 range_start 和 range_end")
        return insertmod.plan_replace_range(
            path, fold_md, int(body.range_start), int(body.range_end)
        )
    raise insertmod.InsertError(f"未知目标 kind：{kind}")


def _proposal_put(pid: str, rec: dict[str, Any]) -> None:
    _proposals[pid] = rec
    proposalsmod.put(pid, rec)


def _proposal_get(pid: str) -> dict[str, Any] | None:
    rec = _proposals.get(pid)
    if rec is not None:
        return rec
    rec = proposalsmod.get(pid)
    if rec is not None:
        _proposals[pid] = rec
    return rec


def _proposal_del(pid: str) -> None:
    _proposals.pop(pid, None)
    proposalsmod.delete(pid)


def request_exit() -> None:
    """SIGTERM 本进程，让 uvicorn 收尾。pytest 必须 patch 掉，否则 TestClient 会把自己杀掉。"""
    os.kill(os.getpid(), signal.SIGTERM)


@app.get("/v1/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "llm": llm_public_status(),
        # 和 llm 平级而不是嵌在里面：前端的 LlmStatus 是个扁平类型，
        # 嵌进去会让「基座配好没」和「快模型配好没」共用一个 ok 字段。
        "fast": configmod.fast_public_status(),
    }


class PreflightBody(LlmOverrideBody):
    """体检哪一套配置。字段全部继承自 LlmOverrideBody —— 体检的必须是
    **读者此刻设置页里填的那套**，和 /v1/chat 逐字同源；另立一套字段
    就会出现「体检过了，真发一轮还是错」。"""

    # 顺带体检快模型那一槽。和 ChatBody.fast 同名同义。
    fast: bool = False


def _slot_report(cfg: LLMConfig | None, why: str, lang: str) -> dict[str, Any]:
    """一槽的体检结论。`why` 是本机就能判出的理由（没钥匙 / 钥匙不对主机），
    非空时**不打网络**——本机都知道配不成，再去问节点是浪费一枪。"""
    if why:
        return {"ok": False, "code": why, "message": msg(_LOCAL_WHY.get(why, why), lang)}
    v = preflight.check(cfg)
    if not v.code:
        return {"ok": True, "code": "", "message": ""}
    return {
        "ok": False,
        "code": v.code,
        "message": tutormod.provider_message_for(
            v.code, lang, kind=v.code, model=v.model or "?", detail=v.detail
        ),
    }


# 本机就能判出来的两种配不成，映射到已有文案。**不新写一份**。
_LOCAL_WHY = {
    configmod.FAST_NO_KEY: "llm.missing_config",
    configmod.FAST_HOST_GAP: "llm.host_mismatch",
    preflight.NO_CONFIG: "llm.missing_config",
}


@app.post("/v1/llm/preflight")
def llm_preflight(body: PreflightBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    """**真往节点打一枪**，回答「这套配置现在到底能不能用」。

    这条存在的理由，是 /v1/health 回答不了读者的问题。它只知道槽里有没有
    钥匙——钥匙是废的、model 在这个节点上不存在、节点没有视觉，它一概显示
    正常。于是设置页说「已保存」，每一轮却撞红字（v0.22.2 的读者报告）。

    调用时机是**配置变了**（失焦 / 存钥匙 / 翻开关 / 开面板），不进轮询：
    一枪 max_tokens=1，便宜，但不该按秒烧。
    """
    base_cfg = body.merged()
    out: dict[str, Any] = {
        "base": _slot_report(base_cfg, "" if base_cfg else preflight.NO_CONFIG, lang)
    }
    if body.fast:
        fast_cfg, fast_why = body.fast_status()
        out["fast"] = _slot_report(fast_cfg, fast_why, lang)
    return out


@app.post("/v1/shutdown")
def shutdown(bg: BackgroundTasks) -> dict[str, str]:
    """设置页「停止」的优雅出口。旧 sidecar 没有这条 → 插件改杀占用端口的进程。"""
    bg.add_task(request_exit)
    return {"status": "stopping"}


@app.put("/v1/llm/key")
def put_llm_key(body: ManagedKeyBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    """v0.18.0：API key 的唯一归宿从 vault 的 data.json 挪到这儿。"""
    key = body.api_key.strip()
    if not key:
        raise HTTPException(400, msg("llm.empty_key", lang))
    configmod.write_managed_key(key, (body.base_url or "").strip())
    return llm_public_status()


@app.delete("/v1/llm/key")
def delete_llm_key() -> dict[str, Any]:
    configmod.clear_managed_key()
    return llm_public_status()


@app.put("/v1/llm/fast-key")
def put_fast_key(body: ManagedKeyBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    """快模型的钥匙。和基座那条同源同形，只是落在托管文件的另一个槽。

    必须是独立通道：快模型在**另一台主机**上，而 merge 那条跨主机保护
    （config._merge_over）见到「换了主机又没自带 key」会直接返回 None。
    """
    key = body.api_key.strip()
    if not key:
        raise HTTPException(400, msg("llm.empty_key", lang))
    configmod.write_fast_key(key, (body.base_url or "").strip())
    return configmod.fast_public_status()


@app.delete("/v1/llm/fast-key")
def delete_fast_key() -> dict[str, Any]:
    configmod.clear_fast_key()
    return configmod.fast_public_status()


@app.post("/v1/maintenance/purge")
def maintenance_purge() -> dict[str, Any]:
    """插件 onload 打这一枪。读者说的是「每次启动插件的时候都要自动清理一下」。

    为什么不能只靠 lifespan：**插件不拉起 sidecar**（`obsidian/src/` 里
    `child_process` 零命中）。sidecar 可能已经在后台跑了好几天，读者重启
    Obsidian 时它的 lifespan 早就跑完了。这个端点是那句话唯一的落点。

    幂等、便宜（实测扫 3389 个文件 0.09 秒）、永不抛。插件那边 fire-and-forget，
    sidecar 没起就静默失败。
    """
    return retention.purge_expired_sessions()


@app.get("/v1/handbooks")
def list_handbooks() -> dict[str, Any]:
    libraries.ensure_default()
    return {"handbooks": [m.__dict__ for m in libraries.list_handbooks()]}


@app.post("/v1/handbooks/import")
def import_handbook(body: ImportBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    try:
        extra = parse_vault_root(body.vault_root)
        meta = libraries.register(
            body.original_path,
            body.handbook_id,
            extra_roots=extra or None,
        )
    except FileNotFoundError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    except (RegisterError, SandboxError) as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    return meta.__dict__


@app.get("/v1/handbooks/{handbook_id}")
def get_handbook(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    meta = _meta_or_404(handbook_id, lang)
    idx = libraries.load_index(handbook_id)
    return {
        **meta.__dict__,
        "n_lines": idx.n_lines,
        "toc": [t.__dict__ for t in idx.toc],
    }


@app.get("/v1/handbooks/{handbook_id}/content")
def get_content(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    meta = _meta_or_404(handbook_id, lang)
    path = Path(meta.original_path)
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    return {
        "original_path": str(path),
        "text": path.read_text(encoding="utf-8"),
        "mtime": path.stat().st_mtime,
    }


@app.get("/v1/handbooks/{handbook_id}/locate")
def locate(handbook_id: str, line: int, lang: str = Depends(req_lang)) -> dict[str, Any]:
    idx = libraries.load_index(handbook_id)
    try:
        sec = idx.locate(line)
    except ValueError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    return sec.__dict__


def _save_and_unlock(sess, lock) -> None:
    """账算了就得落盘。propose 以前一次 save 都没有，读者写回完关掉 Obsidian
    （或者 sidecar 重启）那笔账就永久丢——而「花了钱看不见」正是要解决的
    问题本身。落盘失败不能盖掉正在往外抛的那个异常。"""
    try:
        STORE.save(sess)
    except Exception:
        pass
    lock.release()


def _merged_spend(sess) -> dict[str, Any]:
    """本会话累计 = 主对话 + 写回（在 PenSession 上）+ 深挖（在账本上）。

    **绝不取 STORE.lock_for()**——那把锁在 /v1/chat 整个请求期间被持有，
    抢它会把读者下一次提问顶成 409。probe_store.load() 只是一次文件读。

    这条路径同时是自愈通道：轮询漏掉的、面板关着那段时间发生的深挖花销，
    下一轮 done 一定会补齐。
    """
    book = {k: dict(v) for k, v in (sess.spend or {}).items()}
    try:
        book[metermod.KIND_PROBE] = dict(probe_store.load(sess.session_id).spend)
    except Exception:
        book.setdefault(metermod.KIND_PROBE, metermod.blank())
    return book


def _public_session(sess) -> dict[str, Any]:
    """to_public() 再拼上已经抛给读者看过的深题。

    深题不进 PenSession（后台线程碰它会和请求线程抢 to_dict() 快照），
    所以恢复时得在这一层现拼——否则关一次侧栏，已经花钱挖出来、
    也给读者看过的问题就永久丢了。
    """
    out = sess.to_public()
    try:
        led = probe_store.load(sess.session_id)
        # 只恢复 shown。clicked 是**读者已经问过**的题——恢复回来等于
        # 关一次面板它就复活，成了复读机。
        deep = [q.to_chip() for q in sorted(led.pool, key=lambda x: x.seq)
                if q.state == "shown"]
        # to_public() 里 probe 那格恒为 0，在这儿补上。这是「重开侧栏之后
        # 第三格不归零」的唯一真相来源。
        out["spend"][metermod.KIND_PROBE] = dict(led.spend)
    except Exception:
        deep = []
    if deep:
        seen = {str(c.get("text") or "") for c in out.get("dyn_chips") or []}
        out["dyn_chips"] = [d for d in deep if d["text"] not in seen] + list(out.get("dyn_chips") or [])
    return out


@app.post("/v1/sessions")
def create_session(body: SessionBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    meta = _meta_or_404(body.handbook_id, lang)
    if body.session_id:
        try:
            sess = STORE.get(body.session_id)
            if sess.handbook_id == body.handbook_id:
                # 命中复用也算「碰过」。这条路径不 save，不推 mtime 的话，
                # 读者天天开面板却不发消息的会话会在第 7 天被静默删掉。
                retention.touch(sess.session_id)
                return _public_session(sess)
        except KeyError:
            pass
    # 书名注进 system prompt 的第一句（v0.15.0）。`meta.title` 是
    # `build_index` 从第 1 行 H1 取的，取不到就是文件名——两种都能用。
    sess = STORE.create(body.handbook_id, lang=lang, book_title=str(meta.title or ""))
    return _public_session(sess)


@app.get("/v1/sessions/{session_id}")
def get_session(session_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    try:
        sess = STORE.get(session_id)
    except KeyError as exc:
        raise _no_session(lang) from exc
    retention.touch(session_id)
    return _public_session(sess)


def _footprint(handbook_id: str) -> str:
    """读者这段时间的足迹。diagnose.aggregate 已经把轨迹压好了，白捡。

    措辞上刻意不说「薄弱点」——aggregate 的判据是 hits × CHIP_WEIGHT，
    把「同一次坐下追着问三遍」也算成薄弱，而那明明是正在消化。
    对提问这个用途，「反复回到的地方」才是准确的说法。
    """
    try:
        turns = [t for t in trajectory.load_turns(handbook_id) if trajectory.is_turn(t)]
        rep = diagnosemod.aggregate(turns)
    except Exception:
        return ""
    rows: list[str] = []
    recent = [diagnosemod.label_of(t.get("anchor") or {}) for t in turns[-12:]]
    recent = [r for r in recent if r]
    if recent:
        rows.append("最近读过：" + " / ".join(dict.fromkeys(recent)))
    # 轨迹里每条都存着读者自己打的那句话，先前一个字都没用上——
    # 「user log」被降级成了「读过哪几道题的标题」。而连续追问同一处时，
    # 上面那行去重后会塌成一条，等于没有信息。
    asked_by_reader = []
    for t in reversed(turns):
        text = str(t.get("user_text") or "").strip()
        # 挡掉「看 $E=mc^2$」这种随手敲的渲染测试——真实轨迹里就有两条。
        # 只数汉字和字母：normalize_qkey 不剥 $ = ^ 这些符号，
        # 拿它量长度会把一串数学记号当成有内容。
        if len(re.sub(r"[^\w\u4e00-\u9fff]", "", text)) < 6:
            continue
        if text not in asked_by_reader:
            asked_by_reader.append(text[:80])
        if len(asked_by_reader) >= 6:
            break
    if asked_by_reader:
        rows.append("他自己打字问过（新→旧）：")
        rows += [f"  · {x}" for x in asked_by_reader]
    weak = [w.get("label") for w in (rep.get("weak") or [])[:5] if w.get("label")]
    if weak:
        rows.append("反复回到的地方：" + " / ".join(weak))
    return "\n".join(rows)


def _history(sess, keep: int = 6) -> list[dict[str, str]]:
    """前面几轮的对话摘要，冻结成纯 dict 交给探索线程。

    取 ui_messages 而不是 messages：后者含 tool_call 配对和供应商回传的
    reasoning_content，塞进探索 prompt 会让 token 翻三倍，还会把注意力
    拉回代码细节——而这一层要的恰恰是跳出细节。
    末轮不带，它已经单独在「苏格拉底刚讲了什么」里了。
    """
    rows: list[dict[str, str]] = []
    for m in list(sess.ui_messages or [])[-(keep + 1) : -1]:
        role = str(m.get("role") or "")
        text = str(m.get("text") or "").strip()
        if role not in ("user", "assistant") or not text:
            continue
        rows.append({"role": role, "text": text[:120]})
    return rows


def _maybe_probe(
    sess,
    body: "ChatBody",
    anchor: dict[str, Any],
    path: Path,
    lang: str,
    book_title: str = "",
    custom: "CustomChipSpec | None" = None,
) -> bool:
    """在 done 那一刻决定要不要起一次后台深挖。返回是否真的起了。

    这里所有东西都要**当场冻结**进 job：cfg 尤其不能让线程晚点自己 resolve，
    那会绕过 merge_llm 的跨主机钥匙保护。

    `book_title` 由调用方从 `meta` 传进来，**不从 `sess.book_title` 取**：那个字段
    不落盘（v0.15.0 只把书名固化进 `messages[0]`），从磁盘恢复回来的会话上它是空的——
    而深挖恰恰最常发生在读者聊了几轮之后。走 meta 才是每轮都真。
    """
    try:
        lim = body.merged_limits()
        led = probe_store.load(sess.session_id, sess.handbook_id)
        cfg = body.merged()
        if cfg is None and not (body.base_url or "").strip():
            cfg = configmod.resolve_llm()
        go, _reason = probemod.should_probe(
            enabled=configmod.probe_enabled() and bool(body.deep),
            ok=True,
            chip=body.chip,
            writeback=bool(custom and custom.writeback),
            pending=bool(sess.pending),
            reply=sess.last_assistant or "",
            anchor=anchor,
            probe_calls=led.probe_calls,
            pending_pool=led.pending_count(),
            has_llm=cfg is not None,
            # last_probe_round 在账本上白存了很久（只写不读）——冷却就落在它上面。
            now_round=sess.turns,
            last_probe_round=led.last_probe_round,
            limits=lim,
        )
        if not go or cfg is None:
            return False
        pid = probe_store.try_claim(sess.session_id, sess.handbook_id, sess.turns, lim)
        if pid is None:
            return False
    except Exception:
        return False
    # 坑已经占上了：从这里往下的任何异常都必须先把它放掉，
    # 否则要等五分钟孤儿回收，期间这个会话一次都探不了，
    # 而正在轮询的前端会对着一个永远不会完成的幽灵白等 90 秒。
    try:
        job = probemod.ProbeJob(
            session_id=sess.session_id,
            handbook_id=sess.handbook_id,
            original_path=path,
            anchor=dict(anchor),
            atom=diagnosemod.atom_key(anchor),
            chip=body.chip,
            user_text=body.user_text or "",
            reply=sess.last_assistant or "",
            born_round=sess.turns,
            lang=lang,
            cfg=cfg,
            book_title=book_title,
            limits=lim,
            extra_roots=libraries.extra_roots_for(sess.handbook_id) or [],
            footprint=_footprint(sess.handbook_id),
            history=_history(sess),
            asked=probe_store.asked(sess.session_id),
            # shelf 留空：它要扫登记表、逐本读前 400 行，交给后台线程去做。
            # 这里是 done 事件的构造路径，多一毫秒都是在延长那条流。
        )
        probemod.spawn(job, pid)
        return True
    except Exception:
        # 后台探索炸了不能影响这一轮对话，但坑要还回去
        try:
            probe_store.release(sess.session_id, pid, refund=True)
        except Exception:
            pass
        return False


def _ripe_deep(sess, anchor: dict[str, Any]) -> dict[str, Any]:
    """池子里这一轮该抛出去的题。搭 done 的便车，零额外往返。

    形状和 /deep 端点一致（dyn_chips / deep_cursor），前端两条路共用一套合并。
    读盘失败不能带崩一轮对话——深题没了是遗憾，回复没了是事故。
    """
    try:
        box = probe_store.inbox(
            sess.session_id,
            since=0,
            atom=diagnosemod.atom_key(anchor) if anchor else "",
            level=str(anchor.get("level") or ""),
            now_round=sess.turns,
        )
    except Exception:
        return {}
    items = box.get("items") or []
    if not items:
        return {}
    return {"deep_items": items, "deep_cursor": box.get("cursor", 0)}


@app.post("/v1/sessions/{session_id}/compact")
def compact_chat(session_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    """手动把这场主对话的旧回合折进滚动摘要。pending / 进行中拒绝。"""
    try:
        sess = STORE.get(session_id)
    except KeyError as exc:
        raise _no_session(lang) from exc
    lock, sess = _try_lock_session(sess, lang)
    try:
        if sess.pending:
            raise HTTPException(
                409,
                {"code": "approval_pending", "message": msg("approval.pending", lang)},
            )
        meta = _meta_or_404(sess.handbook_id, lang)
        path = Path(meta.original_path)
        try:
            result = compact_session(
                sess,
                allow_paths=allow_paths_for(path),
                original_path=path,
            )
        except CompactPending:
            raise HTTPException(
                409,
                {"code": "approval_pending", "message": msg("approval.pending", lang)},
            ) from None
        STORE.save(sess)
        out = _public_session(sess)
        out["did"] = result.did
        out["dropped_reads"] = result.dropped_reads
        return out
    finally:
        lock.release()


@app.get("/v1/sessions/{session_id}/deep")
def deep_inbox(
    session_id: str,
    since: int = 0,
    lang: str = Depends(req_lang),
) -> dict[str, Any]:
    """深挖问题的收件箱。

    **绝不取 STORE.lock_for()**：那把锁在 /v1/chat 整个请求期间被持有，
    来抢会把读者下一次提问顶成 409。这里只读 probe_store。

    以会话为键而不是以 probe 为键，是为了让四件事都变简单：探索期间读者又发
    一轮、视图关掉再打开、sidecar 重启（返回 running: [] 而不是语义含混的 404）、
    以及 later 类问题的安放。前端拿 running 为空当终止条件。
    """
    try:
        sess = STORE.get(session_id)
    except KeyError as exc:
        raise _no_session(lang) from exc
    anchor = sess.last_anchor or {}
    return probe_store.inbox(
        session_id,
        since=since,
        atom=diagnosemod.atom_key(anchor) if anchor else "",
        level=str(anchor.get("level") or ""),
        now_round=sess.turns,
    )


@app.post("/v1/chat")
def chat(body: ChatBody, lang: str = Depends(req_lang)) -> StreamingResponse:
    try:
        sess = STORE.get(body.session_id)
    except KeyError as exc:
        raise _no_session(lang) from exc
    if body.chip == "search":
        if sess.pending:
            raise HTTPException(400, msg("approval.pending", lang))

        def search_gen() -> Any:
            yield _sse({"type": "token", "text": SEARCH_REPLY})
            yield _sse(
                {
                    "type": "done",
                    # context_tokens 一直缺着，前端靠 ?? prompt_tokens 才没露馅。
                    "usage": {
                        "context_tokens": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                    },
                    # 这一轮一分钱没花，但会话累计不该因此被前端清零。
                    "spend": _merged_spend(sess),
                    "dynamic_chips": [],
                    "has_substantive": False,
                }
            )

        return StreamingResponse(search_gen(), media_type="text/event-stream")
    lock, sess = _try_lock_session(sess, lang)
    try:
        if sess.pending:
            raise HTTPException(400, msg("approval.pending", lang))
        meta = _meta_or_404(sess.handbook_id, lang)
        idx = libraries.load_index(sess.handbook_id)
        path = Path(meta.original_path)
        # 这一轮从这一刻起算：气泡和轨迹行都记这个时刻，分析时按它对齐。
        asked_at = now_iso()
        t0 = time.monotonic()
        # 读者这句是点了哪种追问：deep（深挖抛的）/ dyn（上一轮回复末尾抛的）/
        # 空（自己打的）。点追问和自己打字是两种学习动作，轨迹里分不出来就白记了。
        picked = ""
        # 读者点幽灵按钮时会以 chip="free" 把原文当 user_text 发回来，
        # 在这里精确匹配即可——这是整个深挖功能唯一的真实质量反馈信号。
        # 顺带认出 open 题：光在生成问题时标记没用，读者点下去以后模型如果
        # 侃侃而谈报一堆假 API，前面所有诚实标记都白搭。
        intent_extra = ""
        try:
            clicked = probe_store.mark_clicked(sess.session_id, body.user_text or "")
            if clicked is not None:
                picked = "deep"
                # 出处在别本的题**不能**走 open 那条「凭记忆讲」——书就在读者库里，
                # 沙箱放行，本轮跨书预算满额，它本可以直接去读。
                intent_extra = probemod.cross_intent(clicked.anchors, lang)
                if not intent_extra and clicked.grounding == "open":
                    intent_extra = probemod.open_intent(lang)
        except Exception:
            pass
        # 书架。v0.8.1 把「跨教材」整个挂在 probe 上，实时这条线一个字都没有——
        # 读者直接开口问「另一本讲什么」，苏格拉底手里明明有 read_file、沙箱也放行，
        # 却不知道有那本书、更不知道路径，只能答「你把路径给我」。
        # 冷启实测 1.2ms（只读每本前 400 行，最多 8 本），命中 60s 缓存 0.002ms。
        try:
            shelf = library_scan.shelf_digest(
                path,
                [m.original_path for m in libraries.list_handbooks()],
                # 必须是 read_file 那把闸，不是全局 handbook_allow_roots()。
                # 后者宽：当前手册是仓库根那本时它会印出 vault 里的书，
                # 苏格拉底照着读就撞在「不在本手册允许的根内」上，白跑一次工具。
                # 走 tutor.read_roots 而不是自己拼：stream_chat 会往里加 REPO_ROOT，
                # 这里少加就漂到反面——仓库根里的教材苏格拉底读得到、书架却不列。
                allow_roots=reading_roots(
                    path, read_roots(libraries.extra_roots_for(sess.handbook_id))
                ),
                with_paths=True,
            )
        except Exception:
            shelf = ""  # 登记表烂了不能把正常对话带崩
        apply_session_lang(sess, lang, book_title=str(meta.title or ""))
        auto_compacted = False
        auto_dropped = 0
        limits = body.merged_limits()
        try:
            chat_images = visionmod.normalize_images(body.images)
        except ValueError as exc:
            raise HTTPException(400, msg(str(exc), lang)) from exc
        if chat_images and not bool(body.vision):
            raise HTTPException(400, msg("vision.disabled", lang))
        if should_auto_compact(sess, limits):
            folded = compact_session(
                sess,
                allow_paths=allow_paths_for(path),
                original_path=path,
            )
            auto_compacted = folded.did
            auto_dropped = folded.dropped_reads
        # 自定义芯片。夹不出东西就是 None，下面整条退回按 chip id 查 CHIP_INTENT——
        # 旧插件不发这个字段走的也是这条路。
        custom = normalize_custom_chip(body.custom_chip)
        # 路由。**放在这儿是因为信号到这一行才全部就位**：custom 刚算完
        # （自定义泡泡的 writeback 只有它知道），而 stream_chat 还没开始。
        # 判定本身是纯函数，零成本。
        fast_cfg, fast_why = body.fast_status() if body.fast else (None, "")
        route, route_why = route_for(
            fast_on=bool(body.fast),
            fast_why=fast_why,
            chip=body.chip,
            writeback=bool(custom and custom.writeback),
            user_text=body.user_text,
            custom_prompt=custom.prompt if custom else "",
        )
        try:
            packet, anchor = build_user_packet(
                idx,
                path,
                selected_text=body.selected_text,
                start_line=body.start_line,
                end_line=body.end_line,
                chip=body.chip,
                user_text=body.user_text,
                asked=[str(c.get("text") or "") for c in sess.last_chips],
                intent_extra=intent_extra,
                shelf=shelf,
                lang=lang,
                compact_fed=bool(sess.compacted),
                custom=custom,
                fallback=sess.last_anchor,
            )
        except ValueError as exc:
            raise HTTPException(400, localized(exc, lang)) from exc
        sess.last_anchor = anchor
        typed = (body.user_text or "").strip()
        if not picked and typed:
            # 上一轮回复末尾抛的两条追问，读者点下去也是 chip="free" + 原文。
            if typed in {str(c.get("text") or "").strip() for c in sess.last_chips}:
                picked = "dyn"
        # 自定义芯片的 label 只有请求里那一份：chip_label() 只认 FIXED_CHIPS，
        # 查不到会**返回 id 本身**，于是气泡上写着 `u.a1b2c3` 并且就这么落盘进
        # ui_messages，换机器、换语言都救不回来。
        #
        # 最后那一手是兜底，不是常规路径：custom 为 None 说明这枚自定义芯片没通过
        # normalize（prompt 消毒完是空的，多半是坏客户端），这一轮的行为**确实**
        # 退化成了 free，所以气泡就照实按 free 标。前端已经不会渲染这种泡泡
        # （chipIsDraft 用消毒后的 prompt 判，和这里同源），但落盘是不可逆的，
        # 值得再挡一层：宁可标成 free，也不能把一串裸 id 永久写进读者的会话。
        shown = typed or (custom.label if custom and custom.label else None) or chip_label(
            "free" if CUSTOM_ID_RE.match(body.chip or "") else body.chip
        )
        # 点芯片时多存一个 chip id：label 是中文且会落盘，只存文本的话，
        # 英文用户恢复旧会话时自己的历史气泡会是中文。存了 id，前端就能查表。
        row: dict[str, Any] = {"role": "user", "text": shown, "ts": asked_at}
        if not typed:
            row["chip"] = body.chip
        if chat_images:
            row["images"] = [{"mime": img["mime"]} for img in chat_images]
        sess.ui_messages.append(row)
        prior_assistant = sess.last_assistant
        STORE.save(sess)
    except Exception:
        lock.release()
        raise

    def gen():
        ok = True
        has_sub = False
        try:
            if auto_compacted:
                yield _sse({"type": "compacted", "dropped_reads": auto_dropped})
            # 开关点亮了，这一轮却没能走快模型——**只在「配坏了」这一类上报**。
            #
            # writeback / write-intent 那几条不报：那是 Fast Mode 正常工作，
            # 每轮都提醒等于把正确行为说成故障。而 fast_why 非空是真的配坏了，
            # 读者不会自己发现（health 的 fast.ok 照样是 True）。
            if fast_why:
                yield _sse({"type": "route", "to": "base", "why": route_why})
            if anchor.get("selection_capped"):
                yield _sse(
                    {
                        "type": "status",
                        "phase": "selection_capped",
                        "text": "selection_capped",
                    }
                )
            for ev in stream_chat(
                sess,
                path,
                packet,
                llm=body.merged(),
                extra_roots=libraries.extra_roots_for(sess.handbook_id),
                allow_env_fallback=not bool((body.base_url or "").strip()),
                lang=lang,
                user_text=body.user_text,
                limits=body.merged_limits(),
                images=chat_images,
                route=route,
                fast_llm=fast_cfg,
            ):
                if ev.get("type") == "done":
                    has_sub = bool(ev.get("has_substantive"))
                    # 探索和「伪流式吐字」并行跑，多数情况读者读完回复时结果已就绪。
                    # 绝不延长这条流——busy=false 要等流关闭，多挂一秒就多冻一秒输入框。
                    ev = {
                        **ev,
                        "deep_running": _maybe_probe(
                            sess, body, anchor, path, lang, str(meta.title or ""), custom
                        ),
                        "spend": _merged_spend(sess),
                        # 每轮都把池子里成熟的题捎出来。**这是 v0.8.1 就设计过
                        # 却一直没实现的那条路**，不补上就是个死锁：
                        # inbox() 是唯一会投递、也是唯一会跑 TTL 过期的地方，
                        # 而它只被 /deep 端点调，那个端点又只在 deep_running
                        # 为真时才被轮询。于是池子攒够 PROBE_PENDING_CAP 条之后
                        # should_probe 永久返回 backlog-full → 不起探索 →
                        # 不轮询 → 不投递也不过期 → 深挖静默停摆，读者只能新开会话。
                        # 走这条路还顺带解决「点过一条之后下一条当轮就顶上来」。
                        **_ripe_deep(sess, anchor),
                    }
                elif ev.get("type") == "error":
                    ok = False
                yield _sse(ev)
        except ProviderError as exc:
            ok = False
            yield _sse({"type": "error", "message": str(exc)})
        except Exception:
            ok = False
            yield _sse({"type": "error", "message": msg("chat.unexpected", lang)})
        finally:
            if sess.last_assistant and sess.last_assistant != prior_assistant:
                sess.ui_messages.append(
                    {"role": "assistant", "text": sess.last_assistant, "ts": now_iso()}
                )
            # 轮次不能挂在「回复内容变了」上：模型偶尔会把同一段话再说一遍，
            # 那仍然是走完的一轮。ui_messages 用内容去重是对的，turns 不是。
            if ok and sess.last_assistant:
                sess.turns += 1
            try:
                STORE.save(sess)
            except Exception:
                pass
            try:
                # 整句、整段、追问、钟——这一行要自足，见 trajectory 模块开头。
                trajectory.append_turn(
                    sess.handbook_id,
                    {
                        "session_id": sess.session_id,
                        "phase": "chat",
                        "asked_at": asked_at,
                        "duration_s": round(time.monotonic() - t0, 1),
                        "chip": body.chip,
                        "picked": picked,
                        "route": str(getattr(route, "value", route)),
                        "user_text": body.user_text or "",
                        "anchor": anchor,
                        "assistant_text": sess.last_assistant or "",
                        "assistant_chars": len(sess.last_assistant or ""),
                        "offered": [str(c.get("text") or "") for c in sess.last_chips],
                        "has_substantive": has_sub or sess.has_substantive,
                        "ok": ok,
                    },
                )
            except Exception:
                pass
            lock.release()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/chat/approve")
def chat_approve(body: ApproveBody, lang: str = Depends(req_lang)) -> StreamingResponse:
    try:
        sess = STORE.get(body.session_id)
    except KeyError as exc:
        raise _no_session(lang) from exc
    lock, sess = _try_lock_session(sess, lang)
    try:
        if not sess.pending or sess.pending.get("id") != body.pending_id:
            raise HTTPException(400, msg("approval.none", lang))
        meta = _meta_or_404(sess.handbook_id, lang)
        path = Path(meta.original_path)
        prior_assistant = sess.last_assistant
        asked_at = now_iso()
        t0 = time.monotonic()
    except Exception:
        lock.release()
        raise

    def gen():
        ok = True
        try:
            for ev in resume_chat(
                sess,
                path,
                allow=body.allow,
                pending_id=body.pending_id,
                llm=body.merged(),
                extra_roots=libraries.extra_roots_for(sess.handbook_id),
                allow_env_fallback=not bool((body.base_url or "").strip()),
                lang=lang,
                # 一轮跨两个请求，approve 也得带——resume_chat 会走 _agent_loop，
                # 那里读 max_tool_rounds 和跨书那两道闸。不带就等于批准之后
                # 后半轮变成一场没有上限的对话。
                limits=body.merged_limits(),
            ):
                if ev.get("type") == "done":
                    # chat 那条带了这个，approve 这条以前没带。文档把 done 称作
                    # 「唯一的自愈通道」——一轮以审批结尾时那条通道是断的。
                    ev = {**ev, "spend": _merged_spend(sess)}
                elif ev.get("type") == "error":
                    ok = False
                yield _sse(ev)
        except ProviderError as exc:
            ok = False
            yield _sse({"type": "error", "message": str(exc)})
        except Exception:
            ok = False
            yield _sse({"type": "error", "message": msg("approve.unexpected", lang)})
        finally:
            if sess.last_assistant and sess.last_assistant != prior_assistant:
                sess.ui_messages.append(
                    {"role": "assistant", "text": sess.last_assistant, "ts": now_iso()}
                )
            try:
                STORE.save(sess)
            except Exception:
                pass
            # 批准后的后半截也要进轨迹——写回是读者最重的学习动作，以前这半截
            # 一个字都没记。phase="approve" 标明它不是新的一轮。
            try:
                changed = bool(sess.last_assistant) and sess.last_assistant != prior_assistant
                trajectory.append_turn(
                    sess.handbook_id,
                    {
                        "session_id": sess.session_id,
                        "phase": "approve",
                        "asked_at": asked_at,
                        "duration_s": round(time.monotonic() - t0, 1),
                        "allow": bool(body.allow),
                        "chip": "",
                        "picked": "",
                        "route": "base",
                        "user_text": "",
                        "anchor": sess.last_anchor,
                        "assistant_text": (sess.last_assistant or "") if changed else "",
                        "assistant_chars": len(sess.last_assistant or "") if changed else 0,
                        "offered": [str(c.get("text") or "") for c in sess.last_chips],
                        "has_substantive": sess.has_substantive,
                        "ok": ok,
                    },
                )
            except Exception:
                pass
            lock.release()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/writeback/propose")
def propose(body: ProposeBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    try:
        sess = STORE.get(body.session_id)
    except KeyError as exc:
        raise _no_session(lang) from exc
    if not sess.last_assistant:
        raise HTTPException(400, msg("writeback.no_answer", lang))
    if not sess.last_anchor:
        raise HTTPException(400, msg("writeback.no_anchor", lang))
    meta = _meta_or_404(sess.handbook_id, lang)
    idx = libraries.load_index(sess.handbook_id)
    path = Path(meta.original_path)
    # 这个端点会写 session.spend（写回那一格的账），所以必须持会话锁——
    # propose_fold_md 的注释写着「请求线程独占」，不持锁那个前提就不成立，
    # 而这里曾经是全仓唯一不持锁却写 session 的路径。
    lock, sess = _try_lock_session(sess, lang)
    # **一个 try/finally 罩住全部**，不是三段各自 `_save_and_unlock`。
    # v0.12.6 之前是后者，而从 `path.read_text` 到最后那次 `_save_and_unlock`
    # 之间**完全裸奔**：`render_new_text` / `unified_diff` / `_content_fp`
    # 任何一处抛出（笔记在这中间被移走、被换成非 UTF-8），`lock.release()`
    # 就不执行。锁一漏，这一场对读者是**永久 409「这场对话还在跑」**，
    # 重启 sidecar 之前解不开——而 v0.12.5 的「永不淘汰持锁会话」还让它
    # 永久占住一个内存槽，对上限完全豁免。
    try:
        try:
            fold = propose_fold_md(
                sess,
                llm=body.merged(),
                allow_env_fallback=not bool((body.base_url or "").strip()),
                lang=lang,
            )
        except RuntimeError as exc:
            raise HTTPException(400, localized(exc, lang)) from exc
        try:
            plan = insertmod.plan_insert(
                idx,
                path,
                line=int(sess.last_anchor["start_line"]),
                fold_md=fold,
                summary_hint=body.summary_hint,
            )
        except insertmod.InsertError as exc:
            raise HTTPException(400, localized(exc, lang)) from exc
        old = path.read_text(encoding="utf-8")
        new = insertmod.render_new_text(old, plan)
        diff = insertmod.unified_diff(old, new, path.name)
        pid = uuid.uuid4().hex
        _proposal_put(
            pid,
            {
                "handbook_id": sess.handbook_id,
                "session_id": sess.session_id,
                "plan": plan,
                "diff": diff,
                "original_path": str(path),
                "content_fp": _content_fp(path),
            },
        )
        out = _public_proposal(pid, path, plan, diff)
        # 带上花销：不带的话前端第三格在写回之后纹丝不动，要等下一轮对话的 done
        # 才补——而这个端点恰恰是读者主动花钱的那一刻。
        out["spend"] = _merged_spend(sess)
        return out
    finally:
        _save_and_unlock(sess, lock)


@app.get("/v1/handbooks/{handbook_id}/outline")
def handbook_outline(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    meta = _meta_or_404(handbook_id, lang)
    path = Path(meta.original_path)
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    return file_outline(path)


@app.post("/v1/writeback/retarget")
def retarget(body: RetargetBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    prop = _proposal_get(body.proposal_id)
    if prop is None:
        raise HTTPException(404, msg("proposal.unknown", lang))
    try:
        sess = STORE.get(prop["session_id"])
    except KeyError as exc:
        raise _no_session(lang) from exc
    path = Path(prop["original_path"])
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(sess.handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    fold = prop["plan"].fold_md
    try:
        plan = _plan_for_target(path, fold, sess, body)
    except insertmod.InsertError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    old = path.read_text(encoding="utf-8")
    new = insertmod.render_new_text(old, plan)
    diff = insertmod.unified_diff(old, new, path.name)
    prop["plan"] = plan
    prop["diff"] = diff
    prop["content_fp"] = _content_fp(path)
    _proposal_put(body.proposal_id, prop)
    return _public_proposal(body.proposal_id, path, plan, diff)


@app.post("/v1/writeback/apply")
def apply(body: ApplyBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    prop = _proposal_get(body.proposal_id)
    if prop is None:
        raise HTTPException(404, msg("proposal.unknown", lang))
    try:
        sess = STORE.get(body.session_id)
    except KeyError as exc:
        raise _no_session(lang) from exc
    if prop["session_id"] != sess.session_id:
        raise HTTPException(403, msg("proposal.wrong_session", lang))
    path = Path(prop["original_path"])
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(sess.handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    expected = prop.get("content_fp")
    if expected and _content_fp(path) != expected:
        raise HTTPException(400, msg("writeback.stale", lang))
    snap = snapshots.take_snapshot(sess.handbook_id, path, "pre-insert")
    try:
        insertmod.apply_insert(path, prop["plan"])
    except insertmod.InsertError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    libraries.refresh_if_stale(sess.handbook_id)
    commit_out = None
    commit_error: str | None = None
    if body.commit:
        commit_msg = body.commit_message or (
            f"pen: 写回 {prop['plan'].level} {prop['plan'].q_title or prop['plan'].beat}"
        )
        try:
            commit_out = gitops.commit_original(path, commit_msg)
        except gitops.GitError as exc:
            commit_error = str(exc)
    _proposal_del(body.proposal_id)
    return {
        "ok": True,
        "original_path": str(path),
        "snapshot": str(snap),
        "commit": commit_out,
        "commit_error": commit_error,
        "bytes": path.stat().st_size,
    }


@app.get("/v1/handbooks/{handbook_id}/snapshots")
def snapshot_status(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    _meta_or_404(handbook_id, lang)
    return snapshots.status(handbook_id)


@app.post("/v1/writeback/rollback")
def rollback(body: RollbackBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    meta = _meta_or_404(body.handbook_id, lang)
    path = Path(meta.original_path)
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(body.handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    try:
        snap = snapshots.undo(body.handbook_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    libraries.refresh_if_stale(body.handbook_id)
    st = snapshots.status(body.handbook_id)
    return {
        "ok": True,
        "restored_from": str(snap),
        "original_path": str(path),
        **st,
    }


@app.post("/v1/writeback/redo")
def redo(body: RollbackBody, lang: str = Depends(req_lang)) -> dict[str, Any]:
    meta = _meta_or_404(body.handbook_id, lang)
    path = Path(meta.original_path)
    try:
        assert_handbook_path(path, extra_roots=libraries.extra_roots_for(body.handbook_id))
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    try:
        snap = snapshots.redo(body.handbook_id, path)
    except FileNotFoundError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    libraries.refresh_if_stale(body.handbook_id)
    st = snapshots.status(body.handbook_id)
    return {
        "ok": True,
        "restored_from": str(snap),
        "original_path": str(path),
        **st,
    }


@app.get("/v1/usage")
def usage_total(handbook_id: str | None = None) -> dict[str, Any]:
    """跨会话的累计用量，给设置页那块统计看的。

    状态行第三格答的是「**这一场**烧了多少」，这里答的是「**一共**烧了多少」。
    两个口径，别混。

    三条规矩：
    · **绝不取 STORE.lock_for()**——那把锁在 /v1/chat 整个请求期间持有，
      抢它会把读者下一次提问顶成 409。这里只读文件。
    · **diagnose.narrate 那格不进累计**（它按 handbook 索引，没有会话可挂，
      只在自己的响应体里）。算进来的话数会对不上，v0.10.0 就写明过。
    · 读坏一个文件不能让整个统计挂掉。同时把「数了几个会话」报出去，
      读者才知道这个数覆盖了多少。

    实测 2925 个会话文件（12 MB）全读一遍 0.37 秒，设置页按需打开够用。
    """
    book = metermod.blank_book()
    counted = 0
    skipped = 0

    def _want(raw: dict[str, Any]) -> bool:
        return not handbook_id or str(raw.get("handbook_id") or "") == handbook_id

    try:
        sess_dir = configmod.PEN_DIR / "sessions"
        for f in sorted(sess_dir.glob("*.json")) if sess_dir.is_dir() else []:
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                skipped += 1
                continue
            if not isinstance(raw, dict) or not _want(raw):
                continue
            counted += 1
            got = raw.get("spend")
            if isinstance(got, dict):
                for kind in (metermod.KIND_CHAT, metermod.KIND_FOLD):
                    book[kind] = metermod.merge(book[kind], got.get(kind))
    except Exception:
        pass

    try:
        for f in sorted(probe_store.probes_dir().glob("*.json")):
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                skipped += 1
                continue
            if not isinstance(raw, dict) or not _want(raw):
                continue
            book[metermod.KIND_PROBE] = metermod.merge(
                book[metermod.KIND_PROBE], raw.get("spend")
            )
    except Exception:
        pass

    return {
        "spend": book,
        "total": metermod.total_book(book),
        "sessions": counted,
        "skipped": skipped,
        "handbook_id": handbook_id or "",
    }


@app.get("/v1/chips")
def chips() -> dict[str, Any]:
    return {"chips": FIXED_CHIPS}


@app.get("/v1/handbooks/{handbook_id}/diagnosis")
def get_diagnosis(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    _meta_or_404(handbook_id, lang)
    turns = trajectory.load_turns(handbook_id)
    report = diagnosemod.aggregate(turns)
    report["handbook_id"] = handbook_id
    return report


@app.post("/v1/handbooks/{handbook_id}/diagnosis/narrate")
def narrate_diagnosis(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    _meta_or_404(handbook_id, lang)
    turns = trajectory.load_turns(handbook_id)
    report = diagnosemod.aggregate(turns)
    report["handbook_id"] = handbook_id
    m = metermod.Meter(kind="diag")
    try:
        text = diagnosemod.narrate(report, m)
    except RuntimeError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    # 这一格**不进「本会话累计」**：诊断按 handbook 索引，没有会话可挂。
    # 只放在这里，谁想看谁看。见 docs/v0.10.0。
    return {"handbook_id": handbook_id, "narrative": text, "spend": m.to_dict()}


# ── 学习画像（v0.25.0）。规则、缓存、算分都在 pen/profile.py，这里只做三件事：
# 找书、拿主模型、把厂商异常翻成 400。──


class ProfileCodeBody(LlmOverrideBody):
    # 处理器里夹到 1..10、看不懂当缺省，**不 422**：面板传个离谱值应得到正常进度。
    max_batches: int | None = None
    force: bool = False


def _clamp_batches(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return profilemod.MAX_BATCHES_DEFAULT
    return max(1, min(10, n))


@app.post("/v1/handbooks/{handbook_id}/profile/code")
def code_profile(
    handbook_id: str, body: ProfileCodeBody, lang: str = Depends(req_lang)
) -> dict[str, Any]:
    """编下一批轮次。**一律主模型**，思考档同主对话；key 只从托管槽 / env 来。

    厂商异常 → 400 `{code, message}`，和 /v1/chat 一个码表；已经编好的那几批
    在抛之前就落盘了，读者下次打开面板从断点续。
    """
    _meta_or_404(handbook_id, lang)
    cfg = body.merged()
    if cfg is None:
        raise HTTPException(400, msg("llm.missing_config_short", lang))
    from openai import OpenAIError

    try:
        result = profilemod.code_next(
            handbook_id,
            cfg,
            limits=body.merged_limits(),
            max_batches=_clamp_batches(body.max_batches),
            lang=lang,
            force=bool(body.force),
        )
    except (OpenAIError, OSError, TimeoutError) as exc:
        raise HTTPException(
            400,
            {
                "code": tutormod.provider_error_code(exc, sent_image=False),
                "message": tutormod.provider_error_message(
                    exc, lang, cfg.model, sent_image=False
                ),
            },
        ) from exc
    return {"handbook_id": handbook_id, **result}


@app.get("/v1/handbooks/{handbook_id}/profile")
def get_profile(handbook_id: str, lang: str = Depends(req_lang)) -> dict[str, Any]:
    """这本书的画像。没缓存也 200（全是 uncoded）——面板靠它决定要不要去编。"""
    meta = _meta_or_404(handbook_id, lang)
    return {
        "handbook_id": handbook_id,
        "title": meta.title,
        **profilemod.report(handbook_id, lang),
    }


@app.get("/v1/profiles")
def list_profiles(vault_root: str | None = None, lang: str = Depends(req_lang)) -> dict[str, Any]:
    """这个库里每本书一行的书架。只读缓存，不调模型。"""
    try:
        roots = parse_vault_root(vault_root)
    except SandboxError as exc:
        raise HTTPException(400, localized(exc, lang)) from exc
    if not roots:
        raise HTTPException(400, msg("profile.vault_root_required", lang))
    return profilemod.overview(roots[0], lang)
