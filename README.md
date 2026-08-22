<div align="center">

<!-- width=840 是 scripts/render-hero.py 出图时的实际 CSS 宽度，在 GitHub 上 1:1 呈现。
     正文里那五张 drawio 图用 width=100%，这里不用，是有意的。
     改了 src/logo.ts 的画之后重跑：python scripts/render-hero.py -->
<img src="docs/img/hero.png" width="840"
     alt="Socrates —— 用 . : s A 3 G &amp; @ 这套 8 级密度字符拼出的苏格拉底半身像，旁边是 SOCRATES 标题和副题 Socrates-agent">

**苏格拉底学习法，深度学习任何一本教材**

**中文** · [English](README.en.md)

![Obsidian 1.5.0+](https://img.shields.io/badge/Obsidian-1.5.0%2B-7c3aed?style=flat-square)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab?style=flat-square)
![MIT](https://img.shields.io/badge/License-MIT-16a34a?style=flat-square)
![desktop only](https://img.shields.io/badge/%E4%BB%85%E6%A1%8C%E9%9D%A2-ea580c?style=flat-square)

</div>

---

> 在 Obsidian 里划中一段，侧栏坐着一个人，先不给你答案，先问你一个问题。
>
> 他手里只有两把工具：**读你的笔记**、**改你的笔记**。改之前必须先读，读完了也只能提议——
> 你点「允许」，磁盘上才会动一个字节。
>
> 他跑在你自己的电脑上。**没有作者的服务器。**

**目录**

[为什么需要它](#1--为什么需要它) ·
[它到底怎么用](#2--30-秒看明白它干什么) ·
[核心功能逐个上例子](#3--核心功能逐个上例子) ·
[系统设计](#4--系统设计) ·
[装 · 用 · 隐私](#5--装--用--隐私) ·
[开发与测试](#6--开发--测试--许可) ·
[Future Works](#7--future-works)

---

## 1 · 为什么需要它

苏格拉底是一个 Obsidian 插件。装上之后，你在笔记正文里划中一段，右边侧栏就以这一段为
话题跟你聊下去；聊出来的结论，它可以写回原文那一段。它只认真对待一件事：一个人啃一本
长手册，书很厚，作者不在场，读者只有你自己。收集灵感、润色文章这些它不做。

这件事的起点是一本 **13083 行、626 KB** 的技术手册，一个人写的，也只有一个人在读。
这种书跳着看没有意义：每一关都压在上一关的结论上，第 7 关里一句「所以这里必须先 read」，
理由埋在第 2 关某个决策的第三层里。真正折磨人的从来不是看不懂，而是卡住的那一刻
**没人可问**。

通用聊天机器人补不上这个位置。它没读过你这本书，你贴一段进去，它给的是一段四平八稳的
解释，正确，但和你手里这本书没有关系。它不知道这个概念在第 2 关是怎么铺垫的，不知道
作者在第 4 关的决策②里已经否掉过一个更朴素的做法，也不知道你三分钟前才刚问过一个
几乎一样的问题。

所以它全部的设计都从一句话长出来：**让一个真读过这本书、而且知道你读到哪儿了的人，
坐在你旁边**。既然是坐在你旁边的人，默认动作就不该是把答案端上来。默认那枚芯片叫
`socratic`，职责是先别揭晓、反过来问你一个问题——一个默认反问的学习工具和一个默认给
答案的学习工具是两种东西，后者替你想完了，前者逼你自己先想一遍。

另外两件事它不靠提示词自觉，**靠代码兜住**。模型想改你笔记里的任何一段，必须在更早的
一轮里真的读过那个文件，这笔账由执行层记，声称读过不算数。会话、快照、深挖账本则全在
你自己机器上的本地目录里，仓库里没有任何埋点；装好之后唯一出网的调用是发给你在设置里
填的那个模型节点，只有插件升级后重新拉起服务那一次，它才会再去取一份新的 sidecar。这
两条各自拦在哪一行，[第 4 章](#4--系统设计)有逐条的实现。

下面所有例子跑的都是另一本书：仓库 [`docs/demo/`](docs/demo/) 里那本 1405 行的
《从零手写 DQN · 强化学习通关手册》。它随仓公开，你可以导进自己的库把后面每一段对话
重跑一遍——这份 README 不打算让你只看着截图相信。

---

## 2 · 30 秒看明白它干什么

一次完整的用法是五步，全都发生在你那篇笔记和右边那条侧栏之间：不用离开 Obsidian，不用
开终端，也不用先把整本书喂给谁。前四步只是读和问，只有最后一步会碰你的磁盘，而且必须
你亲手点头它才碰。

1. 在笔记里划中一段。
2. 打开侧栏点「用当前选区」（命令面板里有同名命令），再点一枚芯片（先别揭晓 /
   当我零基础 / 只举例子），或者直接打字问。
3. 他先读你的笔记，再回答你，或者反过来问你一个问题。
4. 与此同时，后台另起一条线（下面叫「深挖 ◆」）去读同一本书的别处，攒几个更深的问题
   存进池子，每轮最多放一条出来抛给你。
5. 想把解答留在笔记里，他会提议一处编辑；你点「允许」，磁盘上才会动。

<img src="docs/img/shot-08-overview.png" width="100%"
     alt="Obsidian 窗口左右分屏：左边是《从零手写DQN》的正文，第二拍里有一段被划中并高亮；右边是苏格拉底侧栏，正在就那一段来回追问，底部挂着一条带 ◆ 的深挖问题。">

<sup>这一节和下一节的界面图都是真跑出来的，没有摆拍。界面语言跟随 Obsidian，图里是中文界面。
状态行上那句「开发回退 DEEPSEEK_API_KEY」是从源码树跑才有的字样——你正常安装之后，
那里显示的是你在设置页填的那个节点。</sup>

**它不是对任意笔记都一样好用。** 主对话对任何 Markdown 都能跑；但后台深挖要在书里下锚，
需要教材按八拍体例写，每一关得有「第三拍 · 出身」「第七拍 · 实操代码」这样的小节。少了
那些小节，五条深挖轴会直接死掉一条，其余四条照常出题。
[`docs/demo/从零手写DQN.md`](docs/demo/从零手写DQN.md) 是一份可以照抄的样板，为什么非得是
这个体例，见 [4.9 教材无关](#49--教材无关)。

---

## 3 · 核心功能逐个上例子

**场景**：你在读《从零手写 DQN》的 Level 3，划中了第四拍的**决策① 「为什么非要两个网络——
一个冻着、一个在更新」**（第 954–964 行）。这一段把答案拆成了三层：数学、动力学、工程。

以下所有回答都来自 **`deepseek/deepseek-v4-flash`**（经 OpenRouter 节点），
跑在一个隔离的 **sidecar**（插件在你本机拉起的那个 Python 小服务）上。
换任何一个 OpenAI 兼容的 Chat Completions 端点都能跑——**但除这一个组合之外我没实测过**。

这一章每一段输出都是真跑出来的：原始请求体和 SSE 事件流全存在
[`docs/demo/transcripts/`](docs/demo/transcripts/) 里，每处引文下面都挂着对应的 JSON。
落盘之后只动过一件事——把采集环境的临时绝对路径换成 `/Users/you/…`，除此之外一个字符
没改（[说明](docs/demo/transcripts/README.md)）。唯一没随仓公开的是
[3.7](#37--两本书之间) 那两段，它们出自作者本机的旧会话，里面全是私人 vault 路径，
那一节的落款自己写着这件事。

### 3.1 · 先别揭晓 `socratic`

**默认那一枚。** 它读你划的那段，然后**不回答**——它去找那段话里你还没意识到的裂缝。

<img src="docs/img/shot-02-socratic.png" width="100%"
     alt="侧栏里点了「先别揭晓，问我一个问题」之后的回答：它把两个更新式并排摆出来，指出区别不在步长而在「记不记得旧账」，最后反过来问读者哪一个更接近它真正想要的。">

<table><tr><td>

**你**：（划中决策①，点「先别揭晓」，一个字没打）

</td></tr><tr><td>

**苏格拉底**：

> 好，你刚读完了决策①的三层拆法。那一块其实已经把“为什么冻住”讲透了——但有一个裂缝你可能注意到了，也可能没注意到。
>
> 手册说冻住目标之后，这就变回了一个“标准的监督回归问题”。好，那我问你：
>
> **监督回归里，标签得是 ground truth 吧？可 Q_target 还是自己估计出来的——它又不是上帝给的正确答案。那“变回标准监督回归”这个说法，是不是藏了一笔账没算？这笔账谁来还？**

</td></tr></table>

<sup>原文：[`03-chat-socratic.json`](docs/demo/transcripts/03-chat-socratic.json)　10.6 秒</sup>

手册里那句「变回标准监督回归」确实滑过了一件事：监督回归的标签是给定的，
而这里的标签是自己估的。

> **为什么默认是这一枚**：一个学习工具，如果默认动作是「把答案给你」，
> 那它每被用一次，你就少想一次。默认动作是「问你一个问题」，成本完全不同。

### 3.2 · 当我零基础 `explain_zero`

同一段，换一枚芯片，形状完全变了：**TL;DR → (a) 概念对比 → (b) 机制 → (c) 反例 → 两个能跑的例子**。

<details>
<summary><b>展开真实回答（1423 字，30 秒）</b></summary>

> **TL;DR**
>
> **冻住目标网络 = 把 RL 的移动靶子钉在墙上，让它变回普通回归。**
> 在线网络每步更新，目标网络每 C 步才被同步一次。两次同步之间，损失函数里的目标是常数，半梯度变成了真梯度。
>
> **(a) 概念/对比：在动 vs 冻住**
>
> | | 不冻（Level 2 的表格 / 函数逼近） | 冻住（DQN） |
> |---|---|---|
> | 目标里有 θ？ | 有，损失一更新目标也跟着变 | 没有，C 步之内目标不变 |
> | 这是什么问题 | 非定常目标回归（半梯度） | 标准监督回归（真梯度） |
> | 收敛风险 | 正反馈环路→发散 | 环路被切断，只每 C 步接通一瞬间 |
>
> **(c) 反例：C = 1 时就是白冻了**
>
> 如果你让 C = 1（每更新一步就同步一次），那目标网络永远和在线网络一模一样——冻住了个寂寞。你退回到了 2013 年那版 DQN，震不减、环没断，烧着显卡和钱，跟没冻一样。
>
> **两个可运行例子**
>
> 都在第七拍的 `train()` 函数里，用同一个函数体、不同参数跑：
>
> **例子 1：标准 DQN（C = 200）**
>
> ```python
> # 第七拍里就是这样调的
> returns = train(episodes=400, sync_every=200)
> ```
>
> `sync_every=200` 就是 C。在线网络每更新 200 次，才把权重抄一份给 target_net。这 200 步里，目标是死的，梯度是准的。跑完能看到 return 曲线一路上扬。
>
> **例子 2：Double DQN（只改一个参数）**
>
> ```python
> # 同一份代码，打开 double=True
> returns = train(episodes=400, sync_every=200, double=True)
> ```
>
> 手册里 `double` 参数写死了——它只在计算 TD target 时多改了一行：选动作用在线网络，估值用目标网络。你可以对比这两条曲线，看看解耦 selection 和 evaluation 之后，是不是更稳了。

</details>

<sup>原文：[`04-chat-explain-zero.json`](docs/demo/transcripts/04-chat-explain-zero.json)</sup>

**这里有一条约束值得单拿出来说。** `explain_zero` 的 prompt 里写着：例子的名字
**必须对得上该 Level 第七拍里真出现过的东西**，不许自己造。核对一下它给的四个名字：

| 它写的 | 教材里 | 位置 |
| --- | --- | --- |
| `train()` | ✅ | `docs/demo/从零手写DQN.md:1223` |
| `sync_every=200` | ✅ | `:1228`　`sync_every: int = 200,  # ← 这就是 C` |
| `double=True` | ✅ | `:1230`　`double: bool = False,  # 打开就是 Double DQN` |
| `if __name__ == "__main__"` | ✅ | `:1292` |

四个全中。这就是为什么工具要先读书，而不是先答题。

### 3.3 · 直接问 `free`

你自己打字。**注意它仍然不肯直接把答案倒给你**——`socratic` 不只是一枚芯片，是整个 prompt 的底色。

<table><tr><td>

**你**：

> 我在学 DQN，不太理解为什么有一个 Q-network 要固定、另一个在更新。
> 凭什么这样算法就会收敛而不发散？这个 idea 最早是从哪来的、出自哪篇 paper？数学上怎么证明它不会发散？

</td></tr><tr><td>

**苏格拉底**：

> 好问题，三个问题串得很顺。我先不急着全倒给你——你把它们拆开想清楚，比听我念答案值钱得多。
>
> 先说你能自己翻到的部分：
>
> 手册第三拍「出身：这两个技巧分别是谁的」专门讲了 target network 和 replay buffer 各自从哪篇 paper 来的。你翻到那一段，看看它俩是不是同一篇论文提出来的——先搞清楚这个。
>
> 然后你回来看决策④。它已经写得很清楚了：**没有人证明过 DQN 会收敛。** 而且它把“能证明什么”和“不能证明什么”分得清清楚楚。你读完全段之后，可以试着用你自己的话回答我下面这个追问：
>
> **决策④说 DQN 和 FQI 之间隔着一层——DQN 每步只做一次梯度更新、目标每 C 步才同步。那如果 FQI 能证收敛，DQN 只是“靠近”它，这种靠近到底多近才算够近？你自己觉得，C 取多大，你才愿意说“嗯，这差不多就是 FQI 了”？**

</td></tr></table>

<sup>原文：[`05-chat-free-three-questions.json`](docs/demo/transcripts/05-chat-free-three-questions.json)　11.0 秒</sup>

三个问题，它一个都没有直接回答，但**三个都指到了书里的确切位置**：出身在第三拍，
收敛性在决策④，而且它把决策④里最重要的那句话原样端了出来——「没有人证明过 DQN 会收敛。」

同一个问题用英文问一遍，它就肯给全了（[`14-chat-free-en.json`](docs/demo/transcripts/14-chat-free-en.json)）：

> **第二个：哪个论文。**
>
> 翻第三拍（924-943 行）。有个关键区分：2013 年的 workshop paper **没有** target network，2015 年的 Nature **加了**。再往前，Riedmiller 2005 的 NFQ 是直接祖先——那篇的做法是「训完一整轮再换目标」，DQN 把它改成了每 C 步换一次。
>
> **第三个：有没有证明它不会发散。**
>
> 结论是：**没有人证明过 DQN 会收敛**。但如果你把 C 推到极端——两次同步之间把回归训到底——它就变成了 Fitted Q Iteration，那个东西有一套误差界。

那些行号、那些年份、那句引文，全部来自它 `read_file` 读到的正文，不是它的记忆。

### 3.4 · 后台深挖 ◆

你那一轮对话结束的**同一时刻**，另起一条线程，去读同一本书的**别处**，攒出几个
「你现在还问不出来、但再往前走一步就会问」的问题存进池子。它跟你的对话**完全并行**，
你不用等；池子不会一次倒给你，每一轮最多放一条出来（`pen/probe_store.py:42` 的
`MAX_RELEASE_PER_TURN = 1`），侧栏上同时最多挂两条。

<img src="docs/img/shot-03-deep.png" width="100%"
     alt="侧栏底部的芯片区：四枚固定芯片下面，一条带 ◆ 的深挖问题被高亮框起来——「γ 给未来打折，α 给旧账打折，两个折扣一起用，Q 值到底在追什么东西？」">

真跑那一次池子里攒了三条，第三条是这样的：

<table><tr><td>

◆ **深挖**

> 你在 Level 2 学到三个东西凑齐就发散，DQN 砍掉了自举的实时性——那剩下的两样在这个结构下还会不会联手炸给你看？

<sup>`axis: altitude` · `depth: 5` · 锚点跨两关：**Level 2（740–747）+ Level 3（954–964）**<br>
（deadly triad：自举 + 离策略 + 函数逼近，三个凑齐就可能发散。）</sup>

</td></tr><tr><td>

**它为什么抛这一条**（`why` 字段，模型自己填的）：

> 他刚读完决策①的三层拆法，手里正好捏着 Level 2 的 deadly triad。让他把两关的知识挂上钩——DQN 没有杀死发散，只是把那个环从每步都通改成每 C 步通一瞬。读者需要补上这个抽象跳跃。

</td></tr></table>

<sup>原文：[`06b-deep-ledger.json`](docs/demo/transcripts/06b-deep-ledger.json)　池子里 3 条，2 次调用，8043 in / 649 out token</sup>

读者划的是 Level 3 的一段，这个问题把它接回了 Level 2 的 deadly triad——**七百行之前**的内容。
它的判断是：DQN 没有消灭 deadly triad 的三个要素，只是把其中一个的**接通频率**
从每步降到了每 C 步。

**闸门是真的会枪毙题的。** 同一段正文，换成英文界面那一场：2 次调用、2002 个输出 token，
池子里**一条都没留下**——全被 `depth < 4` 这道闸挡了（`pen/probe.py:1001`）。
到第二轮才留下一条：

> NFQ trains until convergence before updating the target, while DQN updates every C steps. Why doesn't DQN just adopt NFQ's approach for guaranteed convergence?

<sup>原文：[`13-deep-en.json`](docs/demo/transcripts/13-deep-en.json)</sup>

**广度由代码封死，不由模型自律**——这一条在 [4.5 后台深挖是一条任务队列](#45--后台深挖是一条任务队列)里会讲透。

### 3.5 · 写回原文，和那道审批闸

<img src="docs/img/shot-04-approval.png" width="100%"
     alt="审批面板：标题「审批这次编辑」，下面是 edit_file 指向的那篇笔记，再下面是「--- 原文 ---」和「+++ 换成 +++」的逐行对照，最底下两个按钮「允许这次编辑」和「拒绝」。面板上写着「模型自己选要换的那一小段。点允许才会改这篇笔记。」">

<img src="docs/img/writeback.drawio.svg" alt="写回：两把工具，三道闸，一个快照栈" width="100%">

你说：「把你刚才讲的『为什么要两个网络』那三层，做成一个折叠块，补在决策① 后面。」

它先 `read_file` 读到带行号的原文，**下一轮**才单独提一次编辑。侧栏弹出审批面板，
`old_string` / `new_string` 逐字对照。它提议的是：

```diff
- 这是本关最核心的一问，把它拆成三层：
+ 这是本关最核心的一问。下面从三个层面拆解，你可以按自己感兴趣的顺序读（点击展开）：
+
+ <details>
+ <summary><b>数学层 · 动力学层 · 工程层 —— 三层拆解</b></summary>
  ...三层原文一字不动...
+
+ </details>
```

<sup>原文：[`07-chat-writeback.json`](docs/demo/transcripts/07-chat-writeback.json)</sup>

**同一个文件，四个时刻的大小和 md5：**

| 时刻 | 文件大小 | md5 |
| --- | --- | --- |
| 提案已发出、审批面板开着 | 96874 | `153a8982b0c7…` |
| **你点「拒绝」之后** | **96874** | **`153a8982b0c7…`** |
| 你点「允许」之后 | 97051 | `551363244889…` |
| **回滚之后** | **96874** | **`153a8982b0c7…`** |

提案挂着的时候，磁盘上**一个字节都没动**；拒绝之后还是没动；回滚之后**逐字节还原成原文**。

<sup>原文：[`08b-approve-deny.json`](docs/demo/transcripts/08b-approve-deny.json)（拒绝）· [`08-approve-allow.json`](docs/demo/transcripts/08-approve-allow.json)（允许）· [`09-snapshots-rollback.json`](docs/demo/transcripts/09-snapshots-rollback.json)（回滚）</sup>

**权限层是可以脱离模型单独验的**，这也是它该有的样子：

| 调用 | 返回 |
| --- | --- |
| `decide("read_file")` | `allow` — 只读自动过 |
| `decide("edit_file")` | `ask` — **每一次**都问人 |
| `decide("bash")` / `decide("write_file")` | `deny` — 没登记的工具一律拒 |
| `read_first_block("edit_file", 教材, read_before=∅)` | ⛔ 挡下 |
| `read_first_block("edit_file", 教材, read_before={别的文件})` | ⛔ 挡下 |
| `read_first_block("edit_file", 教材, read_before={教材})` | ✅ 放行 |

<sup>原文：[`15-permission-gates.json`](docs/demo/transcripts/15-permission-gates.json)　源码：`pen/agent/permissions.py:15`、`:23`</sup>

### 3.6 · 回滚 / 重做

写回之后侧栏上那两个箭头，走的是**快照栈**，不是编辑器的 undo。

<img src="docs/img/shot-05-rollback.png" width="100%"
     alt="侧栏顶部的回退箭头悬停时弹出提示「整篇笔记回到上一版（还能退 1 次）」；下方是完整的对话记录，中间可以看到「翻手册 成功」和「改原文 成功」两条状态行。">

```
GET  /v1/handbooks/dqn-550d425c/snapshots
     → {"can_undo": true, "can_redo": false, "undo_n": 1, "redo_n": 0}

POST /v1/writeback/rollback
     → {"ok": true, "restored_from": ".../snapshots/20260822T015739533075Z-pre-edit.md"}
```

**它按整篇还原，不是只撤你划的那一段。** 因为模型提议的编辑可能同时动了好几处，
按选区回退会留下一个半新半旧的文件。

undo + redo **一共只留 20 版**（`pen/config.py:57` `SNAPSHOT_KEEP = 20`）——
这不是一个无限的历史，是一个能兜住「刚才那下点错了」的深度。

### 3.7 · 两本书之间

书架上不止一本书的时候，苏格拉底会拿到一份**其他教材的摘要 + 路径**，于是他能翻到另一本。

这件事的价值，看一对**同题对照**最清楚。同一个问题——「另一本讲什么」——问了两次。

**书架接上之前**，他老实认账：

> 好问题，但我得先给你交个底：**另一本《通关手册》我没读到，不能瞎编。**
>
> 我能确认的是手上这本。它名字就写明了——`writeback-fixture.md`，fixture，测试夹具。
> 意思是：这本书的真正用途不是「内容」，是**测试「写回」这条链路**。
>
> 至于那本《通关手册》具体讲什么——你把它的路径给我，我去读一眼再回答你。
> 不读就猜，那是江湖骗子干的事。

**书架接上之后**，同一个问题，他先去读：

> 我先去翻一眼那本《通关手册》的开头，别凭标题瞎说。
>
> `read_file` → `1  # 手搓 SWE Agent 通关手册 v2 · 教材级（全册：开篇 + Level 0~6 + Capstone）` …
>
> 看完了，两本册子不是一套书，是两种东西。
> **《通关手册》是正课。** 目标一句话：让你从零手搓出一个轻量 SWE Agent……
> 整本一个比喻：你是师傅，Agent 是记性为零、胆子极大的实习生。

<sup>⚠️ 两段都出自作者本机的真实会话，**没随仓公开**（里面是私人 vault 路径），
所以这是全文**唯一**你没法自己核对的引文。其余每一处引文下面都挂着可点开的 JSON 原文。</sup>

**注意「之前」那一版并不是失败。** 他答「我没读到那本书」是**对的**——那本来就是实情。
这就是为什么书架那一整段在只有一本书的时候会**整段不出现**，而不是写一个「（无）」：
写「（无）」等于替模型确认过「没有别的书」，那是替它撒谎。

还有一条：给书名**必须同时给路径**。
> 光给书名，苏格拉底只会去猜文件名。

这句话现在是 `pen/tests/test_tutor.py` 里一条断言的失败提示。

### 3.8 · 账单

```
GET /v1/usage
```

```json
{
  "spend": {
    "chat":  {"calls": 14, "in_tokens": 189275, "out_tokens": 9872,
              "cached_tokens": 125440, "reasoning_tokens": 4906},
    "probe": {"calls": 4,  "in_tokens": 15892,  "out_tokens": 6923,
              "cached_tokens": 3328,  "reasoning_tokens": 5674},
    "fold":  {"calls": 0,  "in_tokens": 0, "out_tokens": 0}
  },
  "total": 221962, "sessions": 2, "skipped": 0
}
```

<img src="docs/img/shot-07-usage.png" width="100%"
     alt="设置页里的「花销」面板：一共 101,785 token，来自 2,694 场对话；下面分列主对话 59,806、深挖 41,979、写回 0，以及缓存命中 34,432。">

<sup>原文：[`10-usage.json`](docs/demo/transcripts/10-usage.json)——上面 3.1 到 3.6 全部跑完之后的真实账单</sup>

三笔分开记：`chat` 是你正在聊的那条线，`probe` 是后台深挖，`fold` 是写回时的折叠生成。
**分开记是为了能分开限**——后台超支不该掐掉你正在读的那一轮。

**它只数 token，不折算成钱。** 因为汇率是你的：你填的是哪个节点、哪个模型、
有没有折扣、缓存命中算不算钱，只有你知道。

不过你大概想知道一个量级。上面那 22 万 token，按我跑这一轮时那个节点对
`deepseek/deepseek-v4-flash` 的挂牌价（$0.077/M 入 + $0.154/M 出）算：

```
入 205167 × $0.077/M  +  出 16795 × $0.154/M  ≈  $0.018
```

**不到两美分**，而且这是**上限**——它把 12.9 万个缓存命中的 token 当全价算了。
换前沿模型会贵一到两个数量级。

**这个累计数会往下掉**，别以为是 bug——会话是有保质期的（[见 4.8](#48--会话会过期账也跟着走)），
被清掉的那些，它们的账也跟着走了。

---

## 4 · 系统设计

### 4.1 · 三个进程，一条 loopback

<img src="docs/img/arch.drawio.svg" alt="三个进程，一条 loopback" width="100%">

Obsidian 插件（TypeScript）、本机 sidecar（Python / FastAPI）、你自己填的那个模型节点，
一共就这三个，中间那条线是 loopback。插件不打作者的服务器，仓库里没有任何 telemetry
端点；sidecar 只绑 `127.0.0.1`，你在设置里填一个非本机地址，`parseListen` 会直接抛
`new Error("not-loopback")`（`src/sidecar.ts:53`），而不是「尽力而为」地警告一句。

模型调用从你的电脑发出去，发到你填的那个节点，任何 OpenAI 兼容的 Chat Completions
端点都行。真正出本机的还有装和升级那两下：插件建 `~/.socrates-pen/venv` 并从 GitHub
和 PyPI pip 安装 sidecar，之后就全在本地。升级那一下有个前提——插件每次启动先 ping 本机
服务，ping 通就直接接上、什么都不下载；只有 ping 不通、要重新拉起时才比对版本号，发现旧
了才照着新版本号再取一份。

### 4.2 · 工具箱里只有两把

工具箱里只有 `read_file` 和 `edit_file` 两把——**没有 bash，没有 write_file，没有 shell**。
权限也不是一个开关，是三值的：`read_file` 是 allow，自动过；`edit_file` 是 ask，**每一次**
都弹审批；其他任何名字一律 deny，不认识就拒。最后这一条是默认拒绝，不是默认放行，模型
幻觉出一个 `run_command` 来，撞的是墙。

### 4.3 · read-first 是一道硬闸

要改哪一段，必须在**更早一轮**成功读过那个文件；同一批 `tool_calls` 里先 read 再 edit
也照样拦。这条容易被当成过度设计，其实不是——同一批里那两个调用的参数是**同时**生成的，
写 `edit_file` 的 `old_string` 时，`read_file` 的结果还没回来。那仍然是猜，只是猜得比较
像。被拦下来时模型收到的不是一句「拒绝」，是一段说明怎么做才对的话：

> 错误：edit_file 之前必须先成功 read_file 同一路径。请先 read_file 看准带行号的原文
> （格式 N\t原文），下一轮再单独调用 edit_file（不要和 read_file 写在同一批 tool_calls 里）。
> old_string 必须是去掉行号前缀后的纯原文。

<sup>`pen/agent/permissions.py:7` `READ_FIRST_MSG`</sup>

### 4.4 · 沙箱有两套根，读和写不是一回事

**写**只允许改**登记过的那一篇**（`assert_write_target`：目标必须逐字等于登记时的原文路径）。
**读**可以放宽到白名单里的根。`.git` / `.obsidian` / `.env*` 一律拒，两边都拒。

**这两套根宽窄差得很远，得说清楚。** 从 Obsidian 里用，读根是**整个库根**
（外加 sidecar 自己那个根：`read_roots()` 返回的是 `[REPO_ROOT, *extra_roots]`，
`REPO_ROOT` 永远在里面——pip 装的时候它是 site-packages）——
插件登记教材时把 `vaultRoot(app)` 一起发过去（`src/views/PenView.ts:788` →
`pen/libraries.py` 的 `meta.allow_root` → `pen/tutor.py:69` 的 `read_roots()` →
`pen/sandbox.py` 的 `assert_readable`），不需要你手动放宽。实测：登记 `book.md`
之后 `read_file("私人/日记/2026.md")` 是**放行**的。

实际不会发生，是因为提示词只告诉了它当前这篇和书架那几本——**那是行为，不是边界**。
写那一侧才是真闸：连书架上的别本都改不了，只能改登记过的那一篇。

书架的可见性用的是**读根**，不是全局允许根。这条差别听起来很细，但方向很明确：

> 印一条苏格拉底读不到的路径，比不印更糟。

因为那会让他试着去读、失败、然后当着你的面编。

### 4.5 · 后台深挖是一条任务队列

<img src="docs/img/deep-queue.drawio.svg" alt="后台深挖：一条任务队列" width="100%">

**为什么要另起一层。** 一开始追问是搭车产出的——让模型在回答的末尾顺手写两条
`<!--pen:chips -->`。问题是那两条永远纠缠在刚讲过的细节上（「echo 加不加引号」），
而读者真正想问的是架构层面的东西：搭车的追问，视野被那一轮的上下文锁死了。所以深挖
被拆成独立的一层，有自己的 prompt、自己的预算、自己的账本。

<details>
<summary><b>展开：这一层的六条设计约束（每条都有它要治的病）</b></summary>

**① `done` 那一刻起一个 daemon 线程，刻意不用 `ThreadPoolExecutor`。**
`ThreadPoolExecutor` 会注册一个 `atexit` 钩子去 join 所有 worker——一个卡在
网络 IO 上的 worker，能让你按 Ctrl-C 之后等 30 秒。daemon 线程不会。

**② 永远不给它 tools。**
定向读由 **Python 执行**，不是模型自己决定读多少：硬上限 **2 段 × 80 行、最多 2 次**。

> **广度由代码封死，不由模型自律。**

一个能自己决定「我再多读几段」的后台任务，就是一张开着口的账单。

**③ 故意不喂邻域。**
不把读者划中那段的前后文喂给它。理由写在 `pen/probe.py` 的模块头上：

> 那 4000 字符里全是手册自带的入门题（「heredoc 里 <<'EOF' 的引号起什么作用」），
> 模型盯着它们必然产同构题——这才是「echo 加不加引号」的病根。

改喂苏格拉底刚讲的那段话，而且剥掉代码块。

**④ 会话为键的收件箱 + 一个游标。**
`GET /v1/sessions/{sid}/deep?since=N`。前端 3 秒一拍
（`src/deeppoll.ts` `DEEP_POLL_MS = 3000`），最多转 **480 秒**
（`DEEP_POLL_BUDGET_MS`），连失败 3 次放弃（`DEEP_POLL_MAX_FAILS`）。
正常情况下 `running` 一空就停，跑不满这个预算。

**⑤ 成熟度闸门。**
每条题自带 `timing`：`now` 这一轮就能放出来，`later` 留在池子里，**每一轮重新过一次
闸**——你读到那儿了，它才出来。放行之外还有一道节流：一轮最多放一条。

**⑥ 质量靠强制填槽 + 确定性校验，不靠夸模型。**
每条题必须填满 `axis` / `depth` / `grounding` / `anchors` / `why`，
五条轴是封闭集合：

| 轴 | 它要产什么 |
| --- | --- |
| `bridge` | 把两处挂上钩 |
| `tradeoff` | 这里选了 A 否了 B，代价是什么（必须填 `alt`） |
| `vs_real` | 现实里是怎么做的（锚点必须落在「第三拍 · 出身」，白名单校验） |
| `failure` | 什么条件下会炸（必须填 `trigger`） |
| `altitude` | 往上抬一层 |

然后 **Python 验槽，不验措辞**：`depth` 自己打 1–5 分，`depth < 4` 的直接扔
（`pen/probe.py:1001`）。[3.4 节](#34--后台深挖-)里英文那一场，2 次调用 2002 个输出 token
被这道闸全部枪毙，就是它在干活。

</details>

### 4.6 · 配置每请求透传，不写全局槽

一台 sidecar 可能同时伺候两个 vault。设置写进全局槽，就是 A 库的模型串到 B 库去。
所以**所有旋钮跟着每一个请求走**。

一共 **18 个**旋钮（`pen/config.py` 的 `LIMIT_RANGE`）。前后端各有一张范围表，
而 `scripts/check-limits.mjs` 是一道 CI 闸，专门守着这两张表**不许漂**——
前端夹到 30、后端夹到 60，那就是一个只在边界上出现的 bug。

### 4.7 · 成本闸门：分类不分总

<img src="docs/img/gates.drawio.svg" alt="闸门全景：钱一套闸，安全另一套" width="100%">

三个 token 上限**分开记也分开限**：主对话一个、后台深挖一个、跨书阅读一个。分开的理由
很实际——后台超支不该掐掉你正在读的那一轮。三条上限默认全是 0，0 就是不限，判据只有
一句 `cap > 0 and (spent + max(0, headroom)) >= cap`（`pen/meter.py:167`），「0 = 不限」
的全部实现就是 `cap > 0` 这一半。

超限不报错。它给模型追加一句让它收敛的话，不是抛异常，你看到的是一个短一点的回答，
不是一个红色感叹号。**主对话那个不是硬上限**：到线之后还会再出一次答案，因为那一轮
已经开跑了；设置页上就是这么写的，这里也照样写。

### 4.8 · 会话会过期，账也跟着走

| 类型 | 判据 | 留多久 |
| --- | --- | --- |
| 空会话 | `len(messages) <= 1` | **1 天** |
| 聊过的 | 其余 | **7 天** |
| 挂着审批的 | `pending.id` 非空**且不是空会话** | **30 天** |

为什么需要这个：清理上线之前实测，会话目录攒到 **3389 个文件 / 10.4 MB**，
其中 **3371 个是空的**——每划一次词就建一场，绝大多数没等到第一句话就被下一次划词换掉了。

第三档那个「且不是空会话」是后来收紧的。第一版写成「有 pending 就永不删」，
那是一条**无界豁免**：任何带 `pending` 键的文件从此占着盘，清理再怎么跑都动不了它——
**那正是这次要治的病本身**。

<sup>`pen/retention.py`，模块头有完整的实测记录</sup>

### 4.9 · 教材无关

它对任何 Markdown 教材都能用，书名从**你那篇笔记的第 1 行 H1** 注进去。这一句是
`SYSTEM_PROMPT` 的第一句话，也就是每一场会话 `messages[0]` 的全部内容，建场那一刻就
固化并落盘——书名要是写死在里面，模型一上来就被告知它在读一本它没在读的书。真跑那一次
落盘的 `messages[0]` 第一行是：

> 你是苏格拉底，坐在读者旁边，正在带人读一本叫《从零手写 DQN · 强化学习通关手册（全册：开篇 + Level 0~3 + Capstone）》的通关手册。

全文里 **`SWE` 出现 0 次**。

<sup>原文：[`02b-system-prompt.json`](docs/demo/transcripts/02b-system-prompt.json)</sup>

书名要注两次，因为提示词有两条路。后台深挖走的是另一份，它的 user packet 里给了位置、
原话、苏格拉底刚讲的、足迹、书架、已经问过的题，唯独不说这是哪本书——模型只能从关号、
拍名和材料往回推，于是照着提示词里那五个示范问题的名字走，而那五个例子取自别的书。
那一路现在也注书名，走的是和 `messages[0]` 同一套清洗：

```
[你在带读哪本书]
《从零手写 DQN · 强化学习通关手册（全册：开篇 + Level 0~3 + Capstone）》
（下面所有材料都出自这本书。它讲什么，看材料——别从别的书上推。）
```

<sup>`pen/probe.py:build_user_message`</sup>

**但八拍体例留下了，而且是故意的。** 「第三拍 · 出身」「第五拍 · Meta Question 门禁」这些
不是那本书的内容，是**格式契约**：`vs_real` 轴要求锚点落在「出身」那一拍，`examples`
要求例子名对得上「第七拍」。教材体例和深挖算法是咬合的，你的书按这个格式写，深挖才有
地方下锚，[`docs/demo/从零手写DQN.md`](docs/demo/从零手写DQN.md) 就是一份可以照抄的样板。

### 4.10 · 一轮对话，从划词到落字

<img src="docs/img/turn.drawio.svg" alt="一轮对话：从划词到落字" width="100%">

<details>
<summary><b>展开：给开发者的那一层</b></summary>

**规模**（截至 v0.15.11，全部实测）

| 部分 | 规模 |
| --- | --- |
| Python（sidecar，不含测试） | 28 个模块，7814 行 |
| Python 测试 | 8700 行，**487 passed** |
| TypeScript（插件） | 15 个文件，3839 行 |
| HTTP 路由 | 23 条 |
| 配置旋钮 | 18 个 |

**SSE 事件的种类在 JSON 载荷的 `type` 字段里，不在 SSE 的 `event:` 行上。**
这是接这套 API 时第一个会踩的坑。八种：

| `type` | 是什么 |
| --- | --- |
| `status` | 阶段：`writing` / `thinking` / `reading` / `tool` |
| `think` | 思考过程（模型支持时） |
| `token` | 正文，一段一段吐 |
| `tool` | 工具跑完了，带 `name` / `ok` / `detail` |
| `approval` | **要你点允许**，带 `pending_id` / `name` / `args` |
| `spend` | 这一轮花了多少 token |
| `done` | 这一轮结束，带合并后的账 |
| `error` | 出事了，带本地化过的人话 |

**测试闸门**

前端 `npm test` 是五道独立的闸，各守一件事：

| 闸 | 守什么 |
| --- | --- |
| `check-i18n.mjs` | 词表自检——语言解析在真实边界上的那几个坑 |
| `check-poll.mjs` | 深挖轮询的**终止条件**，跑的是编译出来的真代码。少一个终止条件，你关掉面板它还在后台敲 sidecar |
| `check-api.mjs` | HTTP 错误的形状，跑的是 `src/api.ts` 编译出来的真代码 |
| `check-css.mjs` | `styles.css` 的不变量，条条是真踩过的坑（跑一遍会打印当前条数） |
| `check-limits.mjs` | **前后端那两张夹紧表必须逐项相等**——同一道闸的两半 |

`npm run build` = `tsc --noEmit && npm test && esbuild`。三样全过才产 `main.js`。

后端 `python -m pytest pen/tests -q` → **487 passed**，
在任何一个干净 checkout 上都该是这个数（v0.15.1 之前不是，见
[`docs/v0.15.1-公开仓测试开箱45红.md`](docs/v0.15.1-公开仓测试开箱45红.md)）。

**教材索引自检**

```bash
python -m pen.index --check 你的笔记.md
```

它不调模型。同一份输入永远切出同一份索引——**这条性质是所有定位、锚点、回读的地基**。

```
$ python -m pen.index --check docs/demo/从零手写DQN.md
从零手写 DQN · 强化学习通关手册（全册：开篇 + Level 0~3 + Capstone）
path=/Users/you/socrates-pen/docs/demo/从零手写DQN.md
lines=1405 sections=87 qs=21 toc=45
CHECK OK
```

**那五张架构图都是 `.drawio.svg`**——GitHub 当图渲染，用 [draw.io](https://app.diagrams.net/)
打开还能直接改（mxGraph 模型存在根 `<svg>` 的 `content` 属性里）。

</details>

---

## 5 · 装 · 用 · 隐私

### 装

开工前确认三件事：

- **Obsidian 桌面版 1.5.0 或更高**（手机版不行，它要在你机器上起一个 Python 进程）
- **本机 Python 3.11 或更高**（[python.org](https://www.python.org/downloads/)）
- 一个兼容 OpenAI Chat Completions 的 **API Key**

进了社区插件目录之后走**设置 → 社区插件 → 浏览 → Socrates**。在那之前手动装：从
[GitHub Release](https://github.com/xesws/socrates-pen/releases) 下载 `main.js` /
`manifest.json` / `styles.css` 三个文件，拷进 `<你的库>/.obsidian/plugins/socrates-pen/`，
再回**设置 → 社区插件**里启用，Restricted mode 要关掉。

第一次启用会花大约一分钟：插件在 `~/.socrates-pen/venv` 里建一个隔离环境，从本仓库
pip 安装 sidecar，这一步要访问 GitHub 和 PyPI。装完之后**设置 → Socrates** 最上面
那一行会显示本机服务在不在跑。全程不用开终端。

### 用

1. 等设置页最上面显示本机服务**运行中**，没跑就点**启动**；然后在**设置 → Socrates**
   填 API Key，需要的话再改 Base URL、模型、Thinking 档位。
2. 打开一篇笔记**划一段**（实时预览或阅读模式都行），再打开苏格拉底侧栏用当前选区，
   或者用命令面板做同样的事。
3. 想把解答写进原文，就说清楚**插哪 / 换哪**，或者在有了真正解答之后点**写回**。模型
   必须先读文件，再单独一轮提编辑；侧栏弹审批，点**允许**才写盘。
4. **回到上一版 / 重做**按快照栈**整篇**回退，不是只撤选区。

<img src="docs/img/shot-01-splash.png" width="100%"
     alt="刚装好、还没填 Key 时的侧栏：顶上一行写着「sidecar 在，请到设置 → Socrates 填写 API Key」，中间是苏格拉底的字符画肖像和「苏格拉底学习法」字标，底下一排芯片——前三枚是亮的，「查相关论文 / 算法出处」和「把刚才的解答写进手册原文」两枚是灰的。">

<img src="docs/img/shot-06-settings.png" width="100%"
     alt="设置页：最上面是本机服务的启动/停止和运行状态，下面依次是语言、API Key（密码框）、Base URL、模型名、Thinking 档位、后台深挖开关，以及每小时深挖上限等旋钮。">

### 隐私与网络

**它不做什么。** 不索引、不遍历、不上传，它不会扫你的库，也不会把笔记发到任何地方。
实际发生的读取只有两种：你划词的那一篇，和你放上书架的那几本。插件默认只访问
`http://127.0.0.1:8765`，不向作者汇报任何东西；会话、快照、深挖账本全在你自己机器上的
`~/.socrates-pen/` 下。

**但那是行为，不是沙箱保证。** 只读到那两种，是因为提示词里只告诉了它这些。沙箱真正的
读边界是**整个库根**（`.git` / `.obsidian` / `.env*` 除外），库根由插件在登记教材时一并
发过去（`src/views/PenView.ts:788` 把 `vaultRoot(app)` 发给 `POST /handbooks/import`），
不需要你手动放宽。你在对话里直接报出一个路径，它就能读到。写的边界严格得多，见
[4.4 沙箱有两套根](#44--沙箱有两套根读和写不是一回事)。

**写盘那一侧才是真闸。** 任何编辑都要你先批准，不批准就是一个字都不落盘，只问不写是
完全正常的用法。兜住这件事的是审批闸，不是「你没点那枚写回芯片」——在 `free` 里直说
「帮我在那段后面补一句」，模型同样会走到 `edit_file`（`pen/session.py:64`），侧栏照样
弹审批，[3.5](#35--写回原文和那道审批闸) 那段拒绝演示用的正是 `free` 芯片
（[`08b-approve-deny.json`](docs/demo/transcripts/08b-approve-deny.json)）。真正的闸在
`pen/agent/permissions.py:18-19`：`edit_file` 恒为 `ask`，对每一枚芯片都成立。

**剩下三件要你自己留意。** 一，API Key 存在本库的 `.obsidian/plugins/socrates-pen/data.json`
里，这个库要是进了 Sync / iCloud / git，钥匙会跟着走。二，网络只在两处发生：装的时候从
GitHub 和 PyPI 拉 sidecar 和依赖到 `~/.socrates-pen`，插件升级后重新拉起服务时会再取一
次；此外就是模型调用，由这个本机进程发出，发到你填的那个节点。三，禁用插件不会停掉
sidecar，那个 Python 进程还在跑，下次启用能立刻用；但设置页那个「停止」只管本次启用里由
插件自己拉起的那个进程，要是它是上一次启用留下来的，按钮不会动它，得自己去杀掉占着那个
端口的进程。

### 装不上 / 起不来

第一次启用要建 venv 再 pip 安装，这一步最容易出事。按顺序查：

1. **设置页最上面那一行说什么。** 显示「未运行」就先点**启动**，错误会直接印在那里。
2. **本机 Python 够不够新。** 终端里 `python3 --version`，要 3.11 或更高；macOS 自带的
   可能还是 3.9，去 [python.org](https://www.python.org/downloads/) 装一个。
3. **venv 建坏了就整个删掉重来。** `rm -rf ~/.socrates-pen/venv`，回设置页点启动它会重建；
   `~/.socrates-pen/` 下还存着你的会话和快照，**别删整个目录**。
4. **端口被占。** 默认 `127.0.0.1:8765`，`lsof -nP -iTCP:8765 -sTCP:LISTEN` 看是谁占着。
5. **还是不行。** 开 Obsidian 的开发者控制台（`Ctrl/Cmd + Shift + I`）看报错，连报错一起
   提到 [Issues](https://github.com/xesws/socrates-pen/issues)。

---

## 6 · 开发 · 测试 · 许可

```bash
npm install
npm test        # 五道闸：i18n / poll / api / css / limits
npm run build   # tsc --noEmit && npm test && esbuild
```

对着一个真库热更新：

```bash
export VAULT_PLUGIN_DIR=/path/to/vault/.obsidian/plugins/socrates-pen
npm run dev
```

没设 `VAULT_PLUGIN_DIR` 时 `npm run dev` 会**直接退出**——防止你把一份陈旧的
副本装进库里。

后端：

```bash
python -m pytest pen/tests -q       # 487 passed
python -m pen.index --check 你的笔记.md
```

想自己复现 [3 章](#3--核心功能逐个上例子)里的所有例子：教材在
[`docs/demo/从零手写DQN.md`](docs/demo/从零手写DQN.md)，
每一步的原始请求体和 SSE 事件流在
[`docs/demo/transcripts/`](docs/demo/transcripts/)。

**许可：MIT**，见 [LICENSE](LICENSE)。

---

## 7 · Future Works

下面是目前还没做完、或已知不好用的地方。

最明显的一条是 **`search` 芯片还是占位**。侧栏上看得见，但是灰的、点不动；鼠标悬上去会
写「P2 才开放，现在不会假装搜过」。先留着，是因为「能不能上网查一下」迟早会有人问；
删掉就等于假装这个需求不存在。

**八拍体例是写死的中文字面量**，`pen/probe.py:76` 是 `THIRD_BEAT = "第三拍"`。你的书里
没有叫「第三拍」的小节，`vs_real` 那条轴就永不出题——英文教材必然如此，中文教材换个叫法
也一样。另外四条轴不受影响，它们各自只看 `level_key` / `alt` / `trigger`，没有一条碰
`beat`；唯一的溢出是那份五条轴共用的 system prompt，锚点白名单空的时候那儿会留一个光杆
标题，不拦任何一条出题，只是一段空承诺。

**英文界面配中文教材时，模型经常仍然用中文回答。** system prompt 本身是中文的，英文只是
尾部追加的一段（`REPLY_IN_ENGLISH`，`pen/session.py:147`），一整篇中文正文压过来，那一段
尾巴顶不住。有意思的是后台深挖那一层不受影响，它的 prompt 更短，英文那一场抛回来的题就是
英文的（见 [`13-deep-en.json`](docs/demo/transcripts/13-deep-en.json)）。

**绕开插件直接打 HTTP 的人会撞一个 400。** `libraries._suggest_id` 保留 CJK 字符（Python
里 `'从'.isalnum()` 是 `True`），吐出来的 id 后端自己不收——`_SAFE_ID` 只认
`[A-Za-z0-9._-]`。插件撞不到这条，`src/selection.ts:30` 的 `handbookIdFromPath` 会把非法
字符全换掉再补一个路径哈希。这一条是写这份 README 时真撞出来的。

剩下的是内部欠账，不影响使用，记在这儿免得忘：`pen/index.py:149` 那条
`if i == 1 or heading.startswith("手搓")` 的后半句是某本手册的专属通道，第 1 行 H1 那条路
对任何书都成立，这个 `or` 只是历史包袱；`pen/config.py:34` 的 `DEFAULT_HANDBOOK` 仍写死着
一个文件名（不存在就空操作，sidecar 照样起）；`src/deeppoll.ts` 的注释写「最多转 5 分钟」，
常量是 480 秒，常量是对的（跨书探索实测跑过 351 秒），注释没跟上。

---

<div align="center">
<sub>

MIT · [xesws/socrates-pen](https://github.com/xesws/socrates-pen)

**中文** · [English](README.en.md)

</sub>
</div>
