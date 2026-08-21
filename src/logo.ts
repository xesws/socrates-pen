/**
 * 启动 Logo 的字符画资产。
 *
 * 肖像与字标都由同一套 8 级密度阶（由稀到密）映射生成——字标不用半块 ▀▄█，
 * 那是像素块风格，跟密度映射的肖像不是一种视觉语言。
 * **同一份画在深浅两个主题下都成立**：密度高的地方是「实心」，深色主题里
 * 浅色字符在暗底上把脸提亮，浅色主题里深色字符在白底上把脸压暗——
 * 形状信息在密度分布里，与字色无关，所以不需要第二份反转资产。
 *
 * 编辑规约（改之前必读，违反会静默错位）：
 *  1. 一律用 String.raw：反斜杠原样保留，不要写成两个。
 *  2. 因此画里禁止出现反引号（String.raw 里它无法转义）。
 *  3. 禁止出现 ${ 。
 *  4. 画必须顶格写；编辑器缩进会变成画面的一部分。
 *  5. 只用 7-bit ASCII 或 U+2580-259F 方块元素。不要用那些
 *     East-Asian-Width = Ambiguous 的字符：用户等宽字体栈一旦落到中文字体，
 *     它们占两列，整幅画会散。lintArt() 在开发期抓这类字符。
 *  6. cols / rows 由 art() 在模块加载时算出，禁止手写常量。
 */

export type AsciiArt = {
  readonly lines: readonly string[];
  readonly cols: number;
  readonly rows: number;
};

function art(raw: string): AsciiArt {
  const lines = raw
    .replace(/^\n/, "")
    .replace(/\n[ \t]*$/, "")
    .split("\n")
    // 只裁行尾：行首空格是画面的一部分，行尾空格不是，也就不依赖编辑器保留它
    .map((l) => l.replace(/[ \t]+$/, ""));
  let cols = 0;
  for (const l of lines) cols = Math.max(cols, l.length);
  return { lines, cols, rows: lines.length };
}

/** 开发期自检：返回非空数组说明画里混进了会破坏对齐的字符。 */
export function lintArt(name: string, a: AsciiArt): string[] {
  const bad: string[] = [];
  a.lines.forEach((l, i) => {
    for (const ch of l) {
      const c = ch.codePointAt(0) ?? 0;
      const ok = (c >= 0x20 && c <= 0x7e) || (c >= 0x2580 && c <= 0x259f);
      if (!ok) {
        bad.push(name + " 第 " + (i + 1) + " 行含不安全字符 U+" + c.toString(16).toUpperCase());
      }
    }
  });
  return bad;
}

/** 助手气泡前的微缩头像。字号约 4px 时是清晰度下限，再小就糊成一团。 */
export const AVATAR = art(String.raw`
        .
      .ooO@O..
    .OO@@@@@o.
    .O@@@@@@.
     .oOooo.
       o. .oo
   .O. @@O@O.
    .  ..OO...
        . .o..
       .o. ..
      ..oo.
`);

export const PORTRAIT_WIDE = art(String.raw`
                           s   . .sAs:   :s:      ..    :.
                                   ..sA:..:        :..   .:.
               ..  :s              ..  A::..      :    :
                .  .s         ::    sG s:s::::..       s:  s3:
          ..        ..      ... s:   sA.:  :ssAsss:.  .    .     .
          :.                  As sss.  . ..:sAGGGG33A::     sA3A..
           .                  .:  :.::..:sA3G&&&&&&&G3G3:   .s::  .s
     ..                         .. :sAGGG&&@@@@@@@@&&GGG3s  ..:.   .s
                       ..::::..::sAAG@&@@@@@@@@@@@@@&GG33G:  .s3A: .::
    :.              .:ssAAAsAAAA33G&@@@@@@@@@@@@@@@@@&G3A33:     ::  .:.
    s    .          :ssA3GGG&GGGG&@@@@@@@@@@@@@@@@@@@&GG3s3A.:s  .G:
    .               .:sA3G&@@&&&&&@@@@@@@@@@@@@@@@@@@&3GGssAA.:: AG  :.
     :               .:sA3GG&&&&&&@@@@@@@@@@@@@@@@@@GGGGA:sAs. A .       :
     :.                .:AA3&&&G&&&&&&@@@@@@@@@@@&&GGG&Gs::::.:s       : .
      .:               ..:sAA3333GGGG&&@&&&&&&&&&GG&&&&3:::: .     .:s::
                    .::.:AGG&GGG3GGGG&&&&@@@@@@@@&&@&&Gs.::.     .s:.      .
                     .:sAsAG@@@&&&&GGG&&@@@@@@@@@@&&&G3s::.       .:.      :
                   .....s33A&@@@@@@&&&&@@@@@@@@@&&&&@&&3:::.    A: .s:  s..
                  .:sAA:. :ss3&@@@@@@&&@@@@&GG&G&@@@@@&GA::.     .sG.  .
    ..               :s33ssA..A3&@GG@3A&&&&&&@@@&3A:...:s:...       .    .
                         :ssssAsA&:s&A 333GG33s.           ..
                                 . .AA            ....    .::        .
                                   :AA.                  :ss:
                                  A&@@3       ..:ss. ::sAG3::
                       .::.       3&@@&AAs. .:sssssAGG3@@&3:
               .:....:sAAs::      3&@@3s&&GGAsAGG&&@&&@@@GA.
                :A3AA3AAsAs:      s@@@33GGG@@&G33G&@@@@&G3s       .
                 .:s3G3G33A:      s&@@&G&&&@@@@@@&&G&G3As.      :
                   .:A33G3A:     :3&@@@&G3s&@@@@@@&&GAs.     .  :
                     sA3G33.  .  A&@@@@&G@3 A&@@@@&GA:     ..::s:
                      :A3A:      s&@&@@G3333::3&&&GA:     .:A:
                      :sA:        ..:s:   A@@3.sGG3s.::...:sssAs.
                      ::.              ..sGG&@&AA33A:s::sAA:s :::
                       .              sA3AAA33GG3AAAAAs:ss3Ass.
                      .. :       ..  :s::s3G3GA3GAsAs3:As::ss..ss
             .       .:            .         :s:GAAA:AAsAs:  ...:.
              ..                      .         ::G3A:A::A:ssA3s
              .                 .sAssA33AA:       ssG3:::. AG. :.
                                   :.  .sAss     . .:33.sA  :  ..
                                 ::s:::::..:.     .:::A3  .:A.:.
                  :              .s.:ss::Ass.        :.ss  .. .
                      .       ::s3ssAAAAAAA3ss:.:.     .:As
                     .s ..   :A A3s.AsAGG3AGGA3:.::.  :  .
                        .:  .:s.3Ass.s3G3GAsG3s3: s::  ..
                        ::  :s.:s::  .s3sAA.:Gs s  .s:  :. .:.
                     .    .  :sA:.    :3AsssAA: .  .::.    .:s:
                     ..       .:As.  .s:. .:. As. ...    .:.:3Ass.
                       .         A. .ss  .     :s      .:::::3GG3A.
                         .:          :ss.:As   ..      .::s::33&&&s
                          .            :s: .s.        .::AssA3GG&&.
                                         : ..      .::::3AAA3G3GGs
`);

export const PORTRAIT_NARROW = art(String.raw`
                .   .::. :.   ..  .
            :        ..:::.   .  .  .
            :     ... ss:.:s::.  .  :.
      .           :.:: ...s3G&G3s.  .A:
                     :A3G&@@@@@&GGA  ::  :
             .:sssss3G@@@@@@@@@@&33A  ::...
  .         :s3G&&GG&@@@@@@@@@@@GGAAs: :3
   .         :A3&@&&&@@@@@@@@@@&GGsss.:..   .
   .          .sAGGG&&&@@&@@@&GG&G::...  ...
             ::AGGGGGGG&&@@@@@&&Gs.:    :.   .
            ..:A3@@@@&&&@@@@@@@@@3:.  .:.:
           .:As:ssG@@@&&@@&&&&G333s:    ::
               ::::AAA3:AAAA:      ..
                     :A            ::
                    .&@A.   ::s:sAG3.
         ....sAs:   .&@3GG3A3G&&&@&A
          :A333A:    &@GGG@@&&&&&Gs.   .
            :3GG:   A@@@&33@@@&Gs.   .:.
             :3A    A&&GA3AsG&Gs   .s:.
             .s.         s&GA33s:.:s:::
                       sA33GG3AAAssAs..
             .           ..:s3AAsAs:.:s.
                    ..:s:.    s3A:::A::
                    .:.:ss:    :As: : .
           .        :::s:s:    .:s....
             .    :sA:AA3A3A::.   :.
              .: ::sA::3G3A3s:.:  .
               . .ss.  sAssA:. ::   ::
                   :s ::    s.    ..:3A:
               .      .:.:.  .   .:s:3&&.
                        ....    .:AA33G3
`);

export const WORDMARK_WIDE = art(String.raw`
 3&&@&s    sG@&@&s     3&&@&A   s&&&&&G:     A&&     G&&&&&&s .&&&&&&3   s&&@@3
3@A  3&.  s@G: .3@A   &@s .3&:  A@A  :@@    .@&@3    ..s@G.:   @&       .@G  s&A
:G&G3s.   G@     &@  s@A        A@GAAA&3    G@ A@:     .@3     @&3333:   3@&GA:
 .:s3&@:  G@.    &@  s@A    :   A@G3@@A    s@&s3@&     :@3     @&ssss.    .sAG@3
G@s  A@s  s@G: .3@A   G@A::&@:  A@s  G@s   &&3G3G@A    :@3     @&    .  :@3  :@&
.3@@@&A    sG@&@&s     A&&&G:   s&s   G&: A&s    G&.   .&3     &&&&&&G   A&@@@G:
`);

export const WORDMARK_NARROW = art(String.raw`
:GGG&s   3&G&G:   A&G&G.  &&GG&A    3@s   AG&&&G: 3&GGG3  sGGG&:
G@:.As  3@:  G&  A@: .3:  @3  G@   :@G&     s@:   G&:::   G@:.As
 AG&Gs  @3   :@: G&       &&G&G:   &G @3    s@    3@GGGs   AG&G:
3A  G@  3@:  G&  s@s :&s  @3 3&:  A@GGG@:   s@.   G&      3A  &@
s&&G&s   3&G&G:   s&G&3   &A  3&. &3   GG   s&.   3&GGGG  s&&G&s
`);


// 开发期自检：模块加载时跑一次。改画的人违反了上面的规约，会在控制台看到
// 具体是哪一幅的哪一行——否则错位是静默的，只表现为「画歪了」。
for (const [name, a] of Object.entries({
  AVATAR,
  PORTRAIT_WIDE,
  PORTRAIT_NARROW,
  WORDMARK_WIDE,
  WORDMARK_NARROW,
})) {
  const bad = lintArt(name, a);
  if (bad.length) console.warn("[socrates-pen] " + bad.join("; "));
}
