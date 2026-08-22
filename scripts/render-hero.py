#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""README 顶部 hero 卡片的出图器。

    python scripts/render-hero.py                 # 出图到 docs/img/hero.png
    python scripts/render-hero.py --variant wide  # 换成 51 行那幅（会打印新的 width=）
    python scripts/render-hero.py --no-quantize   # 跳过调色板压缩，出 RGBA 原图

为什么要有这个脚本：README 顶部原来直接贴 ASCII。这幅画是按 line-height:1 设计的
（styles.css 的 .sp-art），而 GitHub 代码块行高约 1.45，纵向被拉长 45%，看起来就是
「窄、被压缩」。这里用 Chromium 按 line-height:1 排一遍再截图。

依赖（**故意不写进 pyproject**：这是手动工具，不进 CI）：
    pip install playwright pillow && playwright install chromium

必须在装了 Menlo 的 macOS 上跑。排版尺寸钉死 Menlo 的字宽比 1233/2048；换字体卡片宽度
就变，README 里的 <img width> 也要跟着改。脚本会在页内实测，对不上直接退出——不会静默
出一张比例不对的图（那正是当初那幅画被压扁却没人发现的原因）。

CI 是 ubuntu-latest，没有 Menlo，所以这个脚本不在 npm test / 不在 workflow 里。
改了 src/logo.ts 的画之后，手动重跑一次，把 docs/img/hero.png 一起提交。

**字标为什么是真文字而不是 logo.ts 里的 ASCII 字标**（别再改回去了，四种都试过）：

ASCII 密度画的形状信息编码在字符的**密度差异**里，所以单个字符必须能被分辨，
整幅画的形状才立得住。实测在横排右栏（424px 宽）里：

    WORDMARK_NARROW @11px   每列 6.6px   认不出字母
    WORDMARK_NARROW @12px   每列 7.2px   勉强，仍认不出
    WORDMARK_WIDE   @8.8px  每列 5.3px   更糟（格子多 25%，但每格都糊了）
    WORDMARK_WIDE   @16px   每列 9.6px   清楚 —— 但要 674px 宽，横排给不起

而肖像本身接近正方形（宽高比 0.9），竖排里要够宽就必然够高，实测竖排卡片
会长到 580px 且左右大片留白。横排是唯一能把 hero 压在 473px 的布局，
于是「清晰的肖像 + 清晰的 ASCII 字标」在 840px 宽里装不下：
肖像要 ~280px，字标要 ~570px，加间距超出可用的 770px 约 100px。

一个认不出字母的字标就是一片噪点，不如换成读得出来的字。这跟
src/views/splash.ts:74 的判断同源——那里写着「字符画只用在肖像和字标上……
标语是"字"，让它保持可读」；在 README 这个尺寸下，字标也落进了「必须可读」
那一边。肖像仍是字符画；密度阶那一行已经撤掉，位置让给标语（读者定的）。
"""
from __future__ import annotations

import argparse
import colorsys
import html
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "src" / "logo.ts"
OUT = ROOT / "docs" / "img" / "hero.png"

ADV = 1233 / 2048          # Menlo 的 advance/em = 0.60205078125
ADV_TOL = 5e-4
SCALE = 2                  # device_scale_factor：840×411 → 1680×822，Retina 上 1:1
QUANT_COLORS = 192         # 见 quantize() 的 docstring：为什么是 192 / MAXCOVERAGE
TRANSPARENT_IDX = QUANT_COLORS

# README 里 <img width="..."> 的值。实测卡片宽与它对不上就告警——
# 改了 logo.ts 的行列数会走到这里，提醒你同步改中英两份 README。
README_WIDTH = 840

# 真文字字标的字号。写死而不是按右栏宽度反算：反算出来的值会随肖像字号漂，
# 而这个字标是整张卡的视觉主音，尺寸该由设计定、不该是别的参数的副产品。
# 44px 在 fp=13 的右栏（368.5px）里占 86%，离卡片内缘还剩 70px。
WORDTEXT_PX = 44

# 肖像和字标分开选，因为它们受**不同维度**约束：
#   肖像卡在高度上——卡片总高定死字号：高 = 1 + 34 + 31*fp + 34 + 1，fp=13 出 473。
#                    （v0.16.7 把这条上限从 411 抬到 473，+15%：读者要肖像再大一点。
#                    同一条高度下 PORTRAIT_WIDE 的 51 行只能给 403/51 = 7.9px，仍旧糊，
#                    所以 narrow 那幅仍是唯一解。）
#   字标卡在宽度上——右栏宽度由卡片总宽减出来，是个定值。同样一条右栏，
#                    WORDMARK_WIDE(80列) 和 WORDMARK_NARROW(64列) 画出来的字母
#                    **物理尺寸完全相同**，但 WIDE 多 25% 的字符格子去表达同一个字母，
#                    所以形状更准。字号小不等于字看着小——这里比的是格子密度。
PORTRAITS = {"narrow": ("PORTRAIT_NARROW", 13), "wide": ("PORTRAIT_WIDE", 13)}
WORDMARKS = {"wide": "WORDMARK_WIDE", "narrow": "WORDMARK_NARROW", "text": None}

# 这张卡不引用 styles.css：那是插件的运行期样式，会随版本变；README 的产物必须能在
# 任何一个 commit 上原样重出。所以色值全部落成字面量，且每一个都说得出出处。
#
# ACCENT 是**量出来的**：docs/img/shot-01-splash.png 里最常见的饱和紫，出现 260 次，
# 就是「问」按钮和状态点的颜色。它在这里只管边框和分隔线，不再参与底色。
# 标语取插件 i18n 里那一条，不另起炉灶：src/i18n/zh.ts 的 splashTagline。
TAGLINE = "苏格拉底学习法"

ACCENT = (0x8A, 0x5C, 0xF6)

# 底色的锚是 REFERENCE，读者点名的那个紫。它不是审美偏好，是**插件在读者主题下的
# 实际底色**：docs/img/shot-01-splash.png 里 22.5% 的像素恰好是这个值，铺满启动卡
# 的整个上半幅。hero 用它，README 顶上那张卡就和读者打开插件第一眼看到的那张同色。
#
# 上一版（v0.16.5）走的是 styles.css:559-565 那条 color-mix(accent 24%, #241E30)，
# 算出 #3C2D60 = H257 S36 L28。对着参考色 H261 S20 L20 一比：亮 8 个明度点、
# 饱和度高 16 个点。「太浅」说的就是这两条——把高饱和的强调色直接当底色兑，
# 出来的是廉价的紫；压住饱和度、压低明度，才是那个稳重的紫。
REFERENCE = "#30293D"          # H261.0  S19.6  L20.0

# 每档是 linear-gradient 的三个色标：0% / 60% / 100%。
# noble 两档都钉死在参考色的色相与饱和度上（H261 S20），只动明度——
# 整张卡因此只有一个紫，没有第二个色相混进来。
TINTS = {
    # 默认：参考色本身就是最亮的那一端，往尾部压到 L15。卡上没有任何一个像素
    # 比读者点名的那个紫更浅——这一条是对「太浅」的直接回答。
    "noble":      ("#30293D", "#2A2436", "#241F2E"),   # L 20.0 → 17.6 → 15.0
    # 参考色挪到 60% 那个色标，顶端抬到 L24：渐变更看得出来，代价是左上角
    # 比读者给的那个紫亮 4 个明度点。
    "noble-lift": ("#393148", "#30293D", "#272132"),   # L 23.7 → 20.0 → 16.3
    # 照搬 styles.css:559-565 的公式：color-mix(accent 16%/4%, #1E1E1E)。
    # 尾段褪成中性灰——插件嵌在暗色 UI 里该这样，白页上的 hero 不该这样。
    "faithful":   ("#2F2841", "#222027", "#1E1E1E"),
}


def _mix(fg, bg, r):
    """color-mix(in srgb, fg r%, bg)。"""
    return tuple(int(round(f * r + b * (1 - r))) for f, b in zip(fg, bg))


def _hx(c):
    return "#%02X%02X%02X" % c


def _hsl(hexstr):
    c = tuple(int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(*c)
    return h * 360, s * 100, l * 100


# faithful 那三个值是上面那条公式算出来的，写成字面量只为好 grep。在这儿钉住：
# 公式改了、字面量手抖改了，导入时就炸，不会悄悄出一张对不上出处的图。
_FAITHFUL_BASE = (0x1E, 0x1E, 0x1E)
assert TINTS["faithful"] == (_hx(_mix(ACCENT, _FAITHFUL_BASE, 0.16)),
                             _hx(_mix(ACCENT, _FAITHFUL_BASE, 0.04)),
                             _hx(_FAITHFUL_BASE)), TINTS["faithful"]

# noble 两档只准动明度。色相/饱和度一旦漂了，卡上就会出现第二个紫，
# 那正是上一版被读者退回的毛病。
for _name in ("noble", "noble-lift"):
    _rh, _rs, _ = _hsl(REFERENCE)
    for _stop in TINTS[_name]:
        _h, _s, _ = _hsl(_stop)
        assert abs(_h - _rh) <= 1.5 and abs(_s - _rs) <= 1.0, (_name, _stop, _h, _s)


def palette(tint):
    """这一档的渐变、边框、分隔线。"""
    top, mid, base = TINTS[tint]
    return {
        "GRAD": "linear-gradient(150deg,%s 0%%,%s 60%%,%s 100%%)" % (top, mid, base),
        # 18% 是 styles.css:569 的原值（border: color-mix(accent 18%, transparent)）。
        # 实测也对得上：读者那张样图圆角上的描边合成后是 #3D315D，反解 α≈0.16。
        # 上一版用的 0.22 既不是原值也偏亮，跟着这次一起改回去。
        "BORDER": "rgba(%d,%d,%d,0.18)" % ACCENT,
        # 分隔线是 hero 自己加的（插件那张竖排卡没有），没有可照搬的原值；
        # 底色压暗之后 0.30 显得跳，收到 0.26。
        "RULE": "rgba(%d,%d,%d,0.26)" % ACCENT,
    }
TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  html,body { margin:0; padding:0; background:transparent; }
  #card {
    display:flex; align-items:center; gap:__GAP__px;
    width:__CW__px; box-sizing:border-box; padding:34px; border:1px solid __BORDER__; border-radius:16px;
    background:__GRAD__;
    font-family:Menlo, monospace;
  }
  #portrait { flex:none; }
  #meta { flex:1 1 auto; min-width:0; }
  /* line-height:1 是这幅画的设计前提（styles.css 的 .sp-art）。
     GitHub 代码块是 ~1.45，纵向拉长 45%，那正是要修的病。 */
  .art { white-space:pre; line-height:1; letter-spacing:0;
         font-variant-ligatures:none; font-kerning:none;
         font-family:Menlo, monospace; }
  #portrait { font-size:__FP__px; color:#B3B9C0; }
  #meta { display:flex; flex-direction:column; align-items:stretch; }
  #word { font-size:__FW__px; color:#DCDDDE; }
  /* 没有 text-indent：那一条是**居中**布局用来抵消末字后多出的一格字距的
     （插件那张竖排卡就是居中，见 TEMPLATE_COL）。横排右栏是左对齐，
     text-indent 只会把首字往右推——SOCRATES 被推 0.30em = 13.2px，标语被推
     0.34em = 6.8px，副题没有 indent 所以纹丝不动，三行于是各站各的。 */
  #wordtext { font-size:__FT__px; line-height:1; letter-spacing:0.30em;
              color:#DCDDDE; font-weight:500; white-space:nowrap;
              font-family:Menlo, monospace; }
  #rule { height:1px; margin-top:24px; background:__RULE__; }
  /* 层级照插件来（styles.css:613-633）：标语在上，600 粗、0.34em；副题在下，细、0.16em。
     插件那条 text-indent 不搬过来，理由见上面 #wordtext。
     中文钉 Hiragino Sans GB：Menlo 没有汉字，不指定就会跟着系统回退漂。 */
  /* nowrap：英文标语 "The Socratic Method" 在右栏里只剩 20px 余量，一旦静默折行，
     右栏的纵向层级就塌了而且没人会发现。宁可让它溢出到边框外——check_align()
     会把越界量打出来，那是看得见的失败。 */
  #tag  { margin-top:22px; font-size:20px; line-height:1.3; font-weight:600;
          letter-spacing:0.34em; color:#DCDDDE; white-space:nowrap;
          font-family:Menlo,"Hiragino Sans GB",monospace; }
  #sub  { margin-top:11px; font-size:12px; line-height:1.3;
          letter-spacing:0.16em; color:#9AA0A6; white-space:nowrap; }
</style></head>
<body>
<div id="card">
  <div class="art" id="portrait">__PORTRAIT__</div>
  <div id="meta">
    __WORDBLOCK__
    <div id="rule"></div>
    <div id="tag">__TAGLINE__</div>
    <div id="sub">Socrates-agent</div>
  </div>
</div>
</body></html>
"""

TEMPLATE_COL = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  html,body { margin:0; padding:0; background:transparent; }
  #card {
    display:flex; flex-direction:column; align-items:center;
    width:__CW__px; padding:34px 0 30px;
    border:1px solid __BORDER__; border-radius:16px;
    background:__GRAD__;
    font-family:Menlo, monospace;
  }
  /* line-height:1 是这幅画的设计前提（styles.css 的 .sp-art）。
     GitHub 代码块是 ~1.45，纵向拉长 45%，那正是要修的病。 */
  .art { white-space:pre; line-height:1; letter-spacing:0;
         font-variant-ligatures:none; font-kerning:none;
         font-family:Menlo, monospace; }
  #portrait { font-size:__FP__px; color:#B3B9C0; }
  #word { font-size:__FW__px; color:#DCDDDE; margin-top:22px; }
  #rule { height:1px; width:__RW__px; margin-top:24px;
          background:__RULE__; }
  #tag  { margin-top:20px; font-size:18px; line-height:1.3; font-weight:600;
          letter-spacing:0.34em; text-indent:0.34em; color:#DCDDDE;
          font-family:Menlo,"Hiragino Sans GB",monospace; }
  #sub  { margin-top:10px; font-size:11px; line-height:1.3;
          letter-spacing:0.16em; text-indent:0.16em; color:#9AA0A6; }
</style></head>
<body>
<div id="card">
  <div class="art" id="portrait">__PORTRAIT__</div>
  <div class="art" id="word">__WORDMARK__</div>
  <div id="rule"></div>
  <div id="tag">__TAGLINE__</div>
  <div id="sub">Socrates-agent</div>
</div>
</body></html>
"""

# 去掉 text-indent 只把三行拉回同一个**排版原点**，但墨迹不从原点开始：每个字形自带
# 左侧边距（left side bearing），字号越大差得越多。三行字号不同，边距就必然不同，
# 光对齐盒子对不齐眼睛看到的那条竖线。
#
# 所以按 canvas 量出每行首字的真实墨迹起点，再用等量负边距把它拉回栏左缘。目标是
# #rule 的左端——分隔线横跨整栏，是这一栏视觉上的左边界；于是字标、标语、副题、
# 分隔线四条左缘落在同一根竖线上。量出来的值会打印，出图时看得见。
#
# actualBoundingBoxLeft 的符号约定是「向左为正」：墨迹起点在原点右侧时它是负数，
# 所以左边承 = -actualBoundingBoxLeft，要补的负边距 = -左边承 = actualBoundingBoxLeft。
ALIGN_JS = """() => {
  const c = document.createElement('canvas').getContext('2d');
  const out = {};
  for (const id of ['wordtext', 'tag', 'sub']) {
    const el = document.getElementById(id);
    if (!el) continue;                       // ASCII 字标那档没有 #wordtext
    const cs = getComputedStyle(el);
    c.font = cs.fontStyle + ' ' + cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
    const first = el.textContent.trim().charAt(0);
    const bearing = -c.measureText(first).actualBoundingBoxLeft;
    el.style.marginLeft = (-bearing).toFixed(3) + 'px';
    out[id] = Math.round(bearing * 1000) / 1000;
  }
  return out;
}"""

PROBE_JS = """() => {
  const c = document.createElement('canvas').getContext('2d');
  const s = '0'.repeat(64);
  const w = f => { c.font = '100px ' + f; return c.measureText(s).width; };
  // 存在性用 serif 做对照：Menlo 缺席时 'Menlo, serif' 会退回 serif，两者等宽。
  // 只查字宽比不够——Courier 是 1229/2048 = 0.6001，和 Menlo 差 0.2%，会蒙混过关。
  return { serif: w('serif'), menlo: w('Menlo, serif'), adv: w('Menlo, serif') / 6400 };
}"""


def read_art(name: str) -> tuple[str, int, int]:
    """按 logo.ts 里 art() 的同一套裁剪取画，保证 rows/cols 与 TS 算出的一致。"""
    src = LOGO.read_text(encoding="utf-8")
    m = re.search(r"export const %s = art\(String\.raw`(.*?)`\)" % name, src, re.S)
    if not m:
        sys.exit(f"[render-hero] {LOGO} 里找不到 {name}")
    raw = m.group(1).replace("\r", "")
    raw = re.sub(r"^\n", "", raw)                          # 去开头换行
    raw = re.sub(r"\n[ \t]*$", "", raw)                    # 去结尾换行 + 缩进
    lines = [ln.rstrip(" \t") for ln in raw.split("\n")]   # 只裁行尾，行首空格是画面
    return "\n".join(lines), len(lines), max((len(ln) for ln in lines), default=0)


def build_html_col(portrait: str, word: str, fp: float, fw: float,
                   cw: float, rw: float) -> str:
    return (TEMPLATE_COL
            .replace("__CW__", f"{cw:g}")
            .replace("__RW__", f"{rw:g}")
            .replace("__FP__", f"{fp:g}")
            .replace("__FW__", f"{fw:g}")
            .replace("__PORTRAIT__", html.escape(portrait))
            .replace("__WORDMARK__", html.escape(word)))


def build_html(portrait: str, word: str, fp: float, fw: float, gap: float,
               tint: str = "noble", tagline: str = TAGLINE) -> str:
    if word is None:
        block = '<div id="wordtext">SOCRATES</div>'
    else:
        # 画里 & 极多，必须转义，否则 &@@ 之类会被当实体解析
        block = f'<div class="art" id="word">{html.escape(word)}</div>'
    pal = palette(tint)
    return (TEMPLATE
            .replace("__CW__", f"{README_WIDTH:g}")
            .replace("__FT__", f"{WORDTEXT_PX:g}")
            .replace("__GAP__", f"{gap:g}")
            .replace("__FP__", f"{fp:g}")
            .replace("__FW__", f"{fw:g}")
            .replace("__WORDBLOCK__", block)
            .replace("__GRAD__", pal["GRAD"])
            .replace("__BORDER__", pal["BORDER"])
            .replace("__RULE__", pal["RULE"])
            .replace("__TAGLINE__", html.escape(tagline))
            .replace("__PORTRAIT__", html.escape(portrait)))


def render(page_html: str) -> tuple[bytes, dict]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=[
            "--force-color-profile=srgb",   # 不让显示器色彩配置渗进截图
            "--disable-lcd-text",           # 强制灰度抗锯齿，杜绝彩色描边
            "--font-render-hinting=none",   # 逐次可复现
        ])
        page = browser.new_page(viewport={"width": 1200, "height": 700},
                                device_scale_factor=SCALE)
        page.set_content(page_html, wait_until="load")
        page.evaluate("() => document.fonts.ready")

        p = page.evaluate(PROBE_JS)
        if abs(p["serif"] - p["menlo"]) < 0.5:
            browser.close()
            sys.exit("[render-hero] 这台机器上没有 Menlo。本资产按 macOS 的 Menlo 出图；"
                     "装上 Menlo 再跑，或者改 ADV 常量并同步改中英两份 README 的 width=。")
        if abs(p["adv"] - ADV) > ADV_TOL:
            browser.close()
            sys.exit(f"[render-hero] Menlo 字宽比实测 {p['adv']:.6f}，期望 {ADV:.6f}。"
                     "字体版本不对，出的图比例会漂。")

        bearings = page.evaluate(ALIGN_JS)

        card = page.locator("#card")
        box = card.bounding_box()
        # 截元素的 border-box：天然没有多余留白。omit_background=True 让圆角外是真透明——
        # GitHub 的 .markdown-body img 会给图片铺主题底色，所以透明圆角在浅色/深色两套
        # 主题下都能融进页面，一张图通吃。
        png = card.screenshot(omit_background=True, type="png", animations="disabled")
        browser.close()
    box["bearings"] = bearings
    return png, box


def quantize(png: bytes) -> bytes:
    """Skia 给渐变加有序抖动，会把 PNG 从 ~165KB 撑到 ~415KB。

    MAXCOVERAGE 按色彩空间覆盖分配色槽（不是按像素数量），正好保住渐变那条 65 色的
    斜线，把抖动噪声吸回真值：实测 61KB，最大误差 3/255，无色带。换成 MEDIANCUT 会把
    色槽喂给噪声，最大误差 35/255，真的会出色带。192 而非 255：多出的 64 槽全喂噪声，
    且留出空槽给透明索引。
    """
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGBA")
    r, g, b, a = im.split()
    opaque = a.point(lambda v: 255 if v >= 128 else 0)
    # 圆角外的 RGB 是 (0,0,0)，会白占一个色槽：先填成卡片主色再量化。
    # 注意不能写 convert("RGB")——那会把圆角直接填黑。
    rgb = Image.composite(Image.merge("RGB", (r, g, b)),
                          Image.new("RGB", im.size, (34, 35, 39)), opaque)
    try:
        method = Image.Quantize.MAXCOVERAGE
        dither = Image.Dither.NONE
    except AttributeError:                       # Pillow < 9
        method, dither = Image.MAXCOVERAGE, Image.NONE
    q = rgb.quantize(colors=QUANT_COLORS, method=method, dither=dither)
    assert q.getextrema()[1] < TRANSPARENT_IDX, "调色板占满了，没有索引留给透明"
    # quantize(colors=N) 只生成 N 个调色板条目（索引 0..N-1）。要拿索引 N 当透明色，
    # 必须先把调色板撑到 N+1 项——否则 save(transparency=N) 写出的 tRNS 只有 N 字节，
    # 第 N 项落在数组外，圆角会变成不透明的黑色（实测踩过）。
    pal = q.getpalette()[: QUANT_COLORS * 3] + [0, 0, 0]
    q.putpalette(pal)
    # 圆角外统一写成保留索引，用 tRNS 标成全透明。边缘退化成二值 alpha，但那是 2×
    # 分辨率上的 1 设备像素锯齿 = 0.5 CSS px，看不见。
    q.paste(TRANSPARENT_IDX, (0, 0), opaque.point(lambda v: 255 - v))
    buf = io.BytesIO()
    q.save(buf, "PNG", optimize=True, transparency=TRANSPARENT_IDX)
    return buf.getvalue()


def check_align(png: bytes, col_left_css: float, right_limit_css: float) -> bool:
    """从**成图的像素**里量右栏每行文字的墨迹左缘，证明它们真对齐了。

    不看渲染器报了什么，看出来的图。读者退回过一次就是因为这里：三行分别被
    text-indent 推成 +16.0 / +8.0 / +1.0 CSS px 的阶梯，肉眼一看就是没对齐。
    三行左缘互相差超过 1 个设备像素就告警。
    """
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGBA")
    px = im.load()
    W, H = im.size
    x0 = int(round(col_left_css * SCALE))
    lo, hi = max(0, x0 - 40), W - 40

    def bright(x, y):
        q = px[x, y]
        return q[3] == 255 and (q[0] + q[1] + q[2]) / 3 > 140

    bands, start = [], None
    for y in range(H):
        has = any(bright(x, y) for x in range(lo, hi))
        if has and start is None:
            start = y
        elif not has and start is not None:
            if y - start >= 3:
                bands.append((start, y - 1))
            start = None

    lefts, rights = [], []
    for a, b in bands:
        cols = [x for y in range(a, b + 1) for x in range(lo, hi) if bright(x, y)]
        lefts.append(min(cols))
        rights.append(max(cols))
    if not lefts:
        return True
    spread = max(lefts) - min(lefts)
    detail = "  ".join("%+.1f" % ((x - x0) / SCALE) for x in lefts)
    print(f"[render-hero] 右栏 {len(lefts)} 行文字的墨迹左缘（相对栏左缘，CSS px）：{detail}")
    over = max(rights) / SCALE - right_limit_css
    if over > -2:
        print(f"\n  ⚠️  右栏最长那行已经贴到卡片内缘（还差 {-over:.1f} CSS px 就出界）。"
              f"\n      #tag 是 nowrap，再长就会溢出边框被截掉。缩字号或缩 --gap。\n")
        return False
    print(f"[render-hero] 右栏最长一行距卡片内缘还有 {-over:.1f} CSS px")
    if spread > 1:
        print(f"\n  ⚠️  三行没对齐，最大相差 {spread / SCALE:.1f} CSS px。"
              f"\n      多半是又有人往 #wordtext / #tag / #sub 上加了 text-indent，"
              f"\n      或者 ALIGN_JS 的边承补偿没跑到。\n")
        return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=sorted(PORTRAITS), default="narrow",
                    help="肖像用哪一幅；受卡片高度约束，narrow(31行) 才给得起 13px")
    ap.add_argument("--layout", choices=("row", "column"), default="row",
                    help="column = 肖像在上字标在下（插件 splash 的堆叠顺序）")
    ap.add_argument("--fp", type=float, default=0,
                    help="肖像字号；不给就用 PORTRAITS 里的默认值")
    ap.add_argument("--word", choices=sorted(WORDMARKS), default="text",
                    help="字标：text=真文字（默认）；wide/narrow=ASCII 字标，"
                         "横排右栏里认不出字母，见模块头")
    ap.add_argument("--gap", type=float, default=41.5,
                    help="肖像与右栏之间的间距；字标字号由剩下的宽度反算")
    ap.add_argument("--tint", choices=sorted(TINTS), default="noble",
                    help="底色档位：noble=以参考色 %s 为最亮端（默认，仓里那两张）；"
                         "noble-lift=参考色摆中间、顶端再抬一档；"
                         "faithful=照搬插件 color-mix 公式，尾段褪成灰" % REFERENCE)
    ap.add_argument("--tagline", default=TAGLINE,
                    help="卡片上那句标语，默认取 src/i18n/zh.ts 的 splashTagline")
    ap.add_argument("--no-quantize", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    pname, fp = PORTRAITS[args.variant]
    fp = args.fp or fp
    wname = WORDMARKS[args.word]
    portrait, prows, pcols = read_art(pname)
    if wname is None:
        word, wrows, wcols = None, 1, 8
    else:
        word, wrows, wcols = read_art(wname)
    if args.layout == "column":
        # 竖排：字标横跨整张卡片，字号由卡片内宽反算（留 8% 边距，别顶到边框）。
        # 这是横排装不下的东西——横排的右栏最多 463px，而 6 行 80 列的字标要
        # ~570px 才认得出字母；竖排能给到 674px。
        fw = round(README_WIDTH * 0.92 / (wcols * ADV), 3)
    else:
        # 横排：右栏宽度是从卡片总宽减出来的定值，字标字号由它反算。
        right = README_WIDTH - 2 - 68 - pcols * ADV * fp - args.gap
        fw = round(right / (wcols * ADV), 3)
    # text 档不用 fw（#wordtext 的字号是 WORDTEXT_PX），别印一个没生效的数字
    shown_word = f"{wname} @{fw}px" if wname else f"SOCRATES(真文字) @{WORDTEXT_PX}px"
    print(f"[render-hero] {pname} {prows}×{pcols} @{fp}px   {shown_word}")
    print(f"              预期肖像 {pcols * ADV * fp:.2f}×{prows * fp}")

    if args.layout == "column":
        page = build_html_col(portrait, word, fp, fw,
                              README_WIDTH - 2, wcols * ADV * fw)
    else:
        page = build_html(portrait, word, fp, fw, args.gap, args.tint, args.tagline)
    png, box = render(page)
    w = round(box["width"])
    print(f"[render-hero] 卡片实测 {box['width']:.3f} × {box['height']:.3f} CSS px")
    if box.get("bearings"):
        print("[render-hero] 首字左边承（已用等量负边距抵掉）：  "
              + "   ".join(f"{k} {v:+.2f}px" for k, v in box["bearings"].items()))

    if not args.no_quantize:
        raw = len(png)
        png = quantize(png)
        print(f"[render-hero] 量化 {QUANT_COLORS} 色：{raw / 1024:.1f} KB → {len(png) / 1024:.1f} KB")

    if args.layout == "row":
        check_align(png, 1 + 34 + pcols * ADV * fp + args.gap, README_WIDTH - 35)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(png)
    from PIL import Image
    with Image.open(args.out) as im:
        px = im.size
    # --out 可以指到仓库外（出对比图时常这么干），所以 relative_to 要兜住
    try:
        shown = args.out.relative_to(ROOT)
    except ValueError:
        shown = args.out
    print(f"[render-hero] 写入 {shown}  {px[0]}×{px[1]} px  "
          f"{args.out.stat().st_size / 1024:.1f} KB")

    if abs(box["width"] - README_WIDTH) > 1:
        print(f"\n  ⚠️  卡片宽度 {w} ≠ README 里写的 width=\"{README_WIDTH}\"。"
              f"\n      画的行列数变了。把中英两份 README 的 width= 都改成 {w}，"
              f"\n      并把本脚本的 README_WIDTH 也改成 {w}。\n")
    else:
        print(f'[render-hero] README 里保持 <img ... width="{README_WIDTH}">，'
              f"在 GitHub 上 1:1 呈现")


if __name__ == "__main__":
    main()
