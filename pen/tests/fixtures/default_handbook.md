# 通关手册（测试兜底）

# 开篇：这本册子是干什么的

这本册子只服务一件事：让 `pytest` 在任何一个干净 checkout 上都能跑起来。

`pen/config.py` 的 `DEFAULT_HANDBOOK` 原本指向实验室仓那本 13083 行的教材，
公开仓里没有那份文件，`libraries.ensure_default()` 就空操作，45 条测试连地基都没有。
`pen/tests/conftest.py` 把默认手册指到这里，那 45 条才有书可读。

体例照 `pen/index.py` 的确定性索引格式走：Level 分关、八拍分节、
`〔回读：…〕` 收束每个 Q 块。内容不重要，**结构必须真**。

Q 的标题**照抄 `mini_handbook.md`**，不要改：`test_app.py` 有断言逐字对
`**Q1. shell 和 Bash 是什么关系？**` 和 `level == "Level 0"`。

# Level 0 — 终端

## 第一拍 · 📍你在哪一格

你在第 0 关。这一关只有一个目的：让索引器认得出这本书。

## 第二拍 · 铺垫：确定性索引是什么意思

`build_index` 不调模型。它按标题层级和固定标记切块，同一份输入永远切出同一份索引。
这条性质是后面所有定位、锚点、回读的地基。

## 第三拍 · 出身

这里讲 shell 出身。八拍体例是这套工具自己的格式契约，不是某一本书的内容；
`pen/probe.py` 的深挖轴 `vs_real` 会去锚「第三拍 · 出身」，所以这一拍必须在。

## 第四拍 · 设计：本关的设计决策

**决策① 为什么不复用 `mini_handbook.md`**

那本被 8 个测试文件当输入，往里加 `附录` 会动到 `test_index.py` 的计数断言。
新开一本，互不干扰。

**决策② 为什么 `DEFAULT_HANDBOOK_ID` 不改**

测试只断言 id（`test_app.py:43`、`:591`），不断言书的内容。改 id 会白白多红一片。

## 第五拍 · 📝 Meta Question 门禁

> 门禁规则。

**Q1. shell 和 Bash 是什么关系？**
- **TL;DR：** shell 是一类，Bash 是一个。
- **(a) 概念/定义 + 对比：** 一类 vs 实现。
- **(b) 机制/代码层面：** echo $0。
- **(c) 为什么 + 反例：** shebang。
- **(d) Meta Instance：**（点击展开 👇）

<details>

<summary>🔍 实例 1：已有例子</summary>

已有内容。

</details>

〔回读：第三拍 · 出身〕

**Q2. heredoc 的引号？**
- **TL;DR：** 引号冻结展开。
- **(a) 概念/定义 + 对比：** 有引号 vs 无引号。
- **(b) 机制/代码层面：** 词法。
- **(c) 为什么 + 反例：** 日期被写死。
- **(d) Meta Instance：**（点击展开 👇）

<details>

<summary>🔍 实例 1：Q2 例子</summary>

内容。

</details>

〔回读：第四拍 · 设计〕

## 第六拍 · 伪代码

```
读文件 → 按标题切块 → 认出 Level 和 Q → 存索引
```

## 第七拍 · 实操代码

```python
from pen.index import build_index

idx = build_index(path)      # path: Path → HandbookIndex
print(idx.title, len(idx.toc))
```

## 第八拍 · ⚠️坑 / ✅验收 / 承上启下

- ⚠️ 坑：第 1 行不是 H1，书名就退回文件名。
- ✅ 验收：`python -m pen.index --check <file>` 输出 `CHECK OK`。
- 承上启下：下一关看跨关的锚点。

# Level 1 — Python

## 第三拍 · 出身

`〔回读：…〕` 用全角括号，是为了不和正文里的半角括号撞。

## 第五拍 · 📝 Meta Question 门禁

**Q1. venv 是干什么的？**
- **TL;DR：** 隔离解释器。
- **(a) 概念/定义 + 对比：** 房间。
- **(b) 机制/代码层面：** PATH。
- **(c) 为什么 + 反例：** 系统 python。
- **(d) Meta Instance：**（点击展开 👇）

<details>

<summary>🔍 实例 1：venv</summary>

内容。

</details>

〔回读：第三拍 · 出身〕

# 最终通关任务：让 pytest 在干净 checkout 上全绿

克隆一份干净的仓库，什么都不配，直接 `python -m pytest pen/tests -q`。
0 failed 才算过。

# 附录 A：这本册子刻意保留的东西

- `附录` 这一节本身：`test_tutor.py` 的目录断言点名要它出现在 toc 里。
- 两个 Level：书架和跨关锚点的测试需要不止一关。
- Level 0 的两道 Q 逐字照抄 `mini_handbook.md`：`test_app.py` 断言到了标题原文。
- 每个 Q 块都被 `〔回读：…〕` 收束：这是索引器的收束契约。
