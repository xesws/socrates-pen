"""配置体检：**真往节点打一枪**，看这套设置到底能不能用。

## 为什么非得真打

`/v1/health` 回答的是「槽里有没有钥匙」——那是配置的**形状**。读者报的处境
是形状全对、行为全错：钥匙是废的、model 字符串在这个节点上不存在、或者节点
根本没有视觉。这些只有节点自己知道，问本机问不出来。

所以体检发一条真请求，而且**按主对话那一枪的真实形状发**：调
`tutor.llm_create_kwargs()` 本人，于是流式、`stream_options`、工具表、推理档
方言一样不少。内容仍是最小的一句 "hi"；开着「图像理解」时再挂一个 1×1 的
PNG——**节点收不收图，只有把图递过去才知道**。

形状不照抄的理由和别处一样：照抄就是第二个定义点。主对话那一枪将来长出新
字段，探针得自动跟着长，否则「体检过了、真发一轮还是错」会再来一次——
v0.22.5 那个 Google 400 就是这么漏过去的（当时探针不带推理档，状态栏一路绿灯）。

## 花销

输入是一句 "hi" 加三个工具的 schema，约七百个 token；输出**收到第一片就断开**。
1×1 PNG 的 base64 是 68 个字符。按最贵的档算也是千分之几分钱。
它只在**配置变了**的时候跑，不进轮询。

省钱靠断开，不靠 `max_tokens`。原先那个 `max_tokens=1` 是我们自己加的字段，
推理型模型常对最小输出有要求，于是体检招来过读者根本撞不上的 400，再把它报成
读者的配置问题。现在请求体里**一个我们自造的字段都没有**，那类误报按构造消失。

## 错在哪，由谁说

不自己判。异常一律交给 `tutor.provider_error_code()`——那是「节点这一下为什么
失败」的唯一定义点，对话里的红字气泡用的也是它。分家的话，同一个 404 会在
气泡里叫「意料外的错误」、在体检里叫「没这个模型」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pen.config import LLMConfig

# 1×1 透明 PNG。**故意用最小的那张**：体检要便宜，而「收不收图」和图多大无关。
PROBE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# 没有 cfg 可体检。和 provider_* 那组码平级，前端一视同仁地拿来当状态。
NO_CONFIG = "no-config"


@dataclass(frozen=True)
class Verdict:
    """体检结论。

    三个都是字符串，所以**不用元组**——位置解包在这种形状下迟早会拧反，
    而拧反的后果是把节点的原话当成型号名显示出来。
    """

    code: str  # 空串 = 这套配置真的能用
    model: str  # 文案要指名道姓说是哪个型号写错了
    detail: str  # 节点自己那句话。分不出类时全靠它


def probe_messages(vision: bool) -> list[dict[str, Any]]:
    """体检那一枪的 messages。开着视觉就挂图——这是「节点拒不拒图」的全部检法。"""
    if not vision:
        return [{"role": "user", "content": "hi"}]
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{PROBE_PNG}"},
                },
            ],
        }
    ]


def check(cfg: LLMConfig | None, *, timeout: float = 20.0) -> Verdict:
    """体检一套配置。码为空串表示这套配置**真的能用**——不是「看起来填齐了」。

    ## 一枪打不出去时，是哪一样打不出去

    主对话那一枪同时带着四样东西：推理档方言、工具表、流式、`stream_options`。
    任何一样节点不认都是同一个 400，而读者的补救动作完全不同：换厂商、换型号、
    还是这个节点压根做不了 Socrates 要它做的事。

    所以失败时**逐级把这四样减掉**，减到哪一级突然打得出去，就是哪一样的错：

        ① 全形状              成 → 绿灯
        ② −推理档             成 → no-thinking
        ③ −推理档 −工具        成 → no-tools
        ④ ③ −stream_options   成 → no-usage
        ⑤ ④ −stream           成 → no-stream
        减到光了还错 → 沿用原有分类（bad-key / no-model / no-vision / rejected）

    三条纪律：

    - **只有 400 才进梯子。** 401 / 404 / 连不上跟形状无关，减了也是白减，
      一枪定案。
    - **报的原话用第①枪的。** 那才是描述真实拒绝的那句；后面几枪是我们为了
      定位自己造的局面。后面某一枪要是撞上别的错（限流、网络抖、钥匙被撤），
      那是附带伤害不是诊断——**停下来，把第①枪那条拒绝原样报回去**，
      别拿一句更模糊的话换掉一句更具体的。
    - **这一级没减掉任何东西就跳过。** 推理档是 off 时 wire 本来就是空的，
      白打一枪不说，还会把「我们压根没发推理字段」报成 no-thinking。

    梯子只在失败路径上跑，所以日常绿灯**恰好一枪**。
    """
    import httpx
    from openai import OpenAI, OpenAIError

    from pen.agent.registry import schemas
    from pen.tutor import (
        PROVIDER_NO_STREAM,
        PROVIDER_NO_THINKING,
        PROVIDER_NO_TOOLS,
        PROVIDER_NO_USAGE,
        PROVIDER_REJECTED,
        llm_create_kwargs,
        provider_detail,
        provider_error_code,
        thinking_wire,
    )

    if cfg is None:
        return Verdict(NO_CONFIG, "", "")
    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=timeout)
    sent_image = bool(cfg.vision)

    def shot(kwargs: dict[str, Any]) -> tuple[str, str]:
        try:
            resp = client.chat.completions.create(**kwargs)
            if kwargs.get("stream"):
                # **真收一片。** 有些节点的 400 是在第一片里发过来的，不收就
                # 看不见；收到一片就够了，剩下的不要，当场关连接。
                with resp as stream:
                    for _ in stream:
                        break
        # **迭代期间的异常 SDK 不包。** `create()` 抛的 httpx 超时会被包成
        # APITimeoutError，可流一旦开始，读分片的 httpx 错（ReadTimeout /
        # RemoteProtocolError）和坏 SSE 的 JSONDecodeError 是原样往外扔的——
        # 逃出去就是 /v1/llm/preflight 一个 500，而体检的全部职责就是**替读者
        # 把话说清楚**，自己却抛个 500 是最坏的失败方式。
        # 不写成裸 except Exception：那样连我们自己拼错 kwargs 的 TypeError
        # 都会被说成「节点返回了意料外的错误」，把自家的 bug 栽给读者。
        except (
            OpenAIError,
            httpx.HTTPError,
            OSError,
            TimeoutError,
            ValueError,  # json.JSONDecodeError 是它的子类：坏 SSE 分片
        ) as exc:
            # 没挂图就绝不可能是「节点拒收了图片」。**这是按构造排除，不是猜。**
            # v0.22.2 少了这个参数，于是关着视觉的读者被告知「把图像理解关掉」。
            return provider_error_code(exc, sent_image=sent_image), provider_detail(exc)
        return "", ""

    full = llm_create_kwargs(
        cfg, messages=probe_messages(sent_image), tools=schemas()
    )
    code, detail = shot(full)
    if code != PROVIDER_REJECTED:
        return Verdict(code, cfg.model, detail)

    wire = thinking_wire(cfg.model, cfg.thinking, cfg.provider)
    thin = dict(full)
    for blame, drop in (
        (PROVIDER_NO_THINKING, tuple(wire)),
        (PROVIDER_NO_TOOLS, ("tools",)),
        (PROVIDER_NO_USAGE, ("stream_options",)),
        (PROVIDER_NO_STREAM, ("stream",)),
    ):
        thinner = {k: v for k, v in thin.items() if k not in drop}
        if thinner == thin:
            continue
        thin = thinner
        code2, _ = shot(thin)
        if not code2:
            return Verdict(blame, cfg.model, detail)
        if code2 != PROVIDER_REJECTED:
            # 梯子撞上了**别的**（限流、网络抖一下、钥匙刚好被撤）。那是附带
            # 伤害，不是第①枪那个 400 的诊断——拿它当结论，就会把一句具体的
            # 「这个节点不支持工具调用」换成一句「意料外的错误」。
            # 停在这儿，把原来那条拒绝原样报回去。
            break
    return Verdict(PROVIDER_REJECTED, cfg.model, detail)
