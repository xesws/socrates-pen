# 强化学习教材批量测评

已执行结果见 [完整报告](REPORT.md)；教材为 [book.md](book.md)，最终题库为 [bank.json](bank.json)。当前最终运行名为 `final-v3`。

按用户指定，由 GPT-6 Luna 编写 8 章、80 题、1000–2000 行的中文教材；主 agent 审核后，运行现有生产导入与 rubric 工作流。每章 4 单选、3 填空、3 解答，最后一个 H2 为 `meta question`。

## 输入与职责

- `author/`：Luna 的原稿、独立源题清单和作者说明。主 agent 会先审查并要求修订；冻结前的修订会记录。
- `book.md`、`expected.json`：审核后的教材与独立题目字段清单。预期数据不从被测解析器反推。
- `source-review.json`：逐题审核与数值核算记录。审核者是主 agent，不冒充独立人工专家。
- `probe-answers.json`：每章 Q08/Q10 的完整、改述、基础、进阶、错误、矛盾六种作答，共 96 条；模型生成 rubric 前冻结。
- `manifest.json`：数据哈希、dev/test 划分、验收标准和本轮预算。

第 1 章为开发集，其余 7 章为测试集。每题只生成一份 rubric 后用于所有对应作答；不把不同 rubric 的波动当作评分器重复稳定性。真实请求继续使用现有 DeepSeek 配置及 10 个面试 rubric 示例。

## 执行顺序

下列流程描述新数据集首次运行。已有数据已冻结，不能重跑 `freeze` 覆盖；查看现有结果使用 `report --run final-v3`，修改代码或数据需新运行版本。

```sh
python scripts/evaluate-textbook.py check
python scripts/evaluate-textbook.py freeze
python scripts/evaluate-textbook.py rubric --split dev
python scripts/evaluate-textbook.py grade --split dev
# 主 agent 检查开发结果；修改代码时必须使用新 --run，保留原始失败。
python scripts/evaluate-textbook.py rubric --split test
python scripts/evaluate-textbook.py grade --split test
python scripts/evaluate-textbook.py persist
python scripts/evaluate-textbook.py report
```

`persist` 在临时隔离数据库中运行生产构建器，严格重放已记录的真实模型响应，不产生第二轮相同收费请求；任何缺失或变更的请求都失败，不偷偷回退到新模型调用。最终资源保存为 `bank.json`。不会注册或修改用户教材库。

跨运行复用通过 `--reuse-rubrics-from` / `--reuse-grades-from` 显式指定，只接受请求签名完全相同的有效结果；实际调用保留在原运行，结果用 `call_run` 标记来源。重放不虚增API次数或token数。

## 验收与预算

全部 80 题逐字段保真；80 份 rubric 结构合格且逐题内容复核没有未解决实质错误。试答归一化 MAE ≤0.10，至少 90% 作答误差 ≤0.10；每题基础、进阶、完整答案严格递增；错误答案得 0。缺失和失败计入覆盖率，MAE 另明确有效样本分母。

预计 176 次 API 调用；独立 campaign 硬上限 240 次、600 万 tokens，包含重试与修复。并发 4、temperature 0、thinking off、最多 4096 输出 tokens。旧 campaign 及其上限不变。

最终报告分开说明源教材问题、解析缺陷、rubric 规则错误和评分执行错误。教材/题库均为合成测试材料，试答来自主 agent；结果不代表真实学生或真实手写图片表现。
