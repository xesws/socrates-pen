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
会长到 580px 且左右大片留白。横排是唯一能把 hero 压在 411px 的布局，
于是「清晰的肖像 + 清晰的 ASCII 字标」在 840px 宽里装不下：
肖像要 ~280px，字标要 ~570px，加间距超出可用的 770px 约 100px。

一个认不出字母的字标就是一片噪点，不如换成读得出来的字。这跟
src/views/splash.ts:74 的判断同源——那里写着「字符画只用在肖像和字标上……
标语是"字"，让它保持可读」；在 README 这个尺寸下，字标也落进了「必须可读」
那一边。肖像仍是字符画，密度阶那一行也照旧展示画它用的那套字符。
"""
from __future__ import annotations

import argparse
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

# 肖像和字标分开选，因为它们受**不同维度**约束：
#   肖像卡在高度上——卡片总高 411 是硬上限，行数直接定死字号（51 行只能给 6.7px，糊；
#                    31 行能给 11px，字符各是各的形状）。
#   字标卡在宽度上——右栏宽度由卡片总宽减出来，是个定值。同样一条右栏，
#                    WORDMARK_WIDE(80列) 和 WORDMARK_NARROW(64列) 画出来的字母
#                    **物理尺寸完全相同**，但 WIDE 多 25% 的字符格子去表达同一个字母，
#                    所以形状更准。字号小不等于字看着小——这里比的是格子密度。
PORTRAITS = {"narrow": ("PORTRAIT_NARROW", 11), "wide": ("PORTRAIT_WIDE", 11)}
WORDMARKS = {"wide": "WORDMARK_WIDE", "narrow": "WORDMARK_NARROW", "text": None}

# 这张卡不引用 styles.css：那是插件的运行期样式，会随版本变；README 的产物必须能在
# 任何一个 commit 上原样重出。下面的色值是从 styles.css 那套 color-mix 算出来的定值
# （color-mix(in srgb, #7aa2f7 16%, #1E1E1E) = #2D3341，4% = #222327），改插件不影响这里。
TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  html,body { margin:0; padding:0; background:transparent; }
  #card {
    display:flex; align-items:center; gap:__GAP__px;
    width:__CW__px; box-sizing:border-box; padding:34px; border:1px solid rgba(122,162,247,0.22); border-radius:16px;
    background:linear-gradient(150deg,#2D3341 0%,#222327 60%,#1E1E1E 100%);
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
  #wordtext { font-size:44px; line-height:1; letter-spacing:0.30em;
              text-indent:0.30em; color:#DCDDDE; font-weight:500;
              font-family:Menlo, monospace; }
  #rule { height:1px; margin-top:24px; background:rgba(122,162,247,0.28); }
  #sub  { margin-top:22px; font-size:14px; line-height:1.3;
          letter-spacing:0.20em; color:#9AA0A6; }
  #ramp { margin-top:12px; font-size:11px; line-height:1.3;
          letter-spacing:0.34em; color:#868C94; }
</style></head>
<body>
<div id="card">
  <div class="art" id="portrait">__PORTRAIT__</div>
  <div id="meta">
    __WORDBLOCK__
    <div id="rule"></div>
    <div id="sub">Socrates-agent</div>
    <div id="ramp">. : s A 3 G &amp; @</div>
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
    border:1px solid rgba(122,162,247,0.22); border-radius:16px;
    background:linear-gradient(150deg,#2D3341 0%,#222327 60%,#1E1E1E 100%);
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
          background:rgba(122,162,247,0.28); }
  #sub  { margin-top:20px; font-size:13px; line-height:1.3;
          letter-spacing:0.34em; text-indent:0.34em; color:#9AA0A6; }
  #ramp { margin-top:11px; font-size:10px; line-height:1.3;
          letter-spacing:0.30em; text-indent:0.30em; color:#868C94; }
</style></head>
<body>
<div id="card">
  <div class="art" id="portrait">__PORTRAIT__</div>
  <div class="art" id="word">__WORDMARK__</div>
  <div id="rule"></div>
  <div id="sub">Socrates-agent</div>
  <div id="ramp">. : s A 3 G &amp; @</div>
</div>
</body></html>
"""

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


def build_html(portrait: str, word: str, fp: float, fw: float, gap: float) -> str:
    if word is None:
        block = '<div id="wordtext">SOCRATES</div>'
    else:
        # 画里 & 极多，必须转义，否则 &@@ 之类会被当实体解析
        block = f'<div class="art" id="word">{html.escape(word)}</div>'
    return (TEMPLATE
            .replace("__CW__", f"{README_WIDTH:g}")
            .replace("__GAP__", f"{gap:g}")
            .replace("__FP__", f"{fp:g}")
            .replace("__FW__", f"{fw:g}")
            .replace("__WORDBLOCK__", block)
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

        card = page.locator("#card")
        box = card.bounding_box()
        # 截元素的 border-box：天然没有多余留白。omit_background=True 让圆角外是真透明——
        # GitHub 的 .markdown-body img 会给图片铺主题底色，所以透明圆角在浅色/深色两套
        # 主题下都能融进页面，一张图通吃。
        png = card.screenshot(omit_background=True, type="png", animations="disabled")
        browser.close()
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=sorted(PORTRAITS), default="narrow",
                    help="肖像用哪一幅；受卡片高度约束，narrow(31行) 才给得起 11px")
    ap.add_argument("--layout", choices=("row", "column"), default="row",
                    help="column = 肖像在上字标在下（插件 splash 的堆叠顺序）")
    ap.add_argument("--fp", type=float, default=0,
                    help="肖像字号；不给就用 PORTRAITS 里的默认值")
    ap.add_argument("--word", choices=sorted(WORDMARKS), default="text",
                    help="字标：text=真文字（默认）；wide/narrow=ASCII 字标，"
                         "横排右栏里认不出字母，见模块头")
    ap.add_argument("--gap", type=float, default=41.5,
                    help="肖像与右栏之间的间距；字标字号由剩下的宽度反算")
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
    print(f"[render-hero] {pname} {prows}×{pcols} @{fp}px   "
          f"{wname or 'SOCRATES(文字)'} @{fw}px")
    print(f"              预期肖像 {pcols * ADV * fp:.2f}×{prows * fp}")

    if args.layout == "column":
        page = build_html_col(portrait, word, fp, fw,
                              README_WIDTH - 2, wcols * ADV * fw)
    else:
        page = build_html(portrait, word, fp, fw, args.gap)
    png, box = render(page)
    w = round(box["width"])
    print(f"[render-hero] 卡片实测 {box['width']:.3f} × {box['height']:.3f} CSS px")

    if not args.no_quantize:
        raw = len(png)
        png = quantize(png)
        print(f"[render-hero] 量化 {QUANT_COLORS} 色：{raw / 1024:.1f} KB → {len(png) / 1024:.1f} KB")

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
