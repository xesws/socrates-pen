# MQ schema v1 真实测评材料

- [测评结论](REPORT.md)
- [固定教材](book.md)：六个有题单元与两个无题单元。
- [原题 JSON](bank.json) + [图片附件](assets/)：仅脚本提取，不扩题。
- [预期字段](expected.json)、[标准 rubric](gold-rubrics.json)、[80 份作答](answers.json)、[视觉配对](visual.json)。
- [冻结清单](manifest.json)：输入文件哈希及开发/正式分组。
- [逐份 rubric 复核](rubric-review.json)、[真实请求与结果](runs/)。

## 不调用模型的检查与导出

```sh
python scripts/evaluate-practice.py --phase check
python scripts/import-mq.py evals/mq_v1/book.md --out /tmp/mq-export
python -m pytest pen/tests/test_mq_importer.py -q
```

导出目录包含 bank.json 和相对路径图片。该 JSON 含教师答案，不能直接作为作答前的公共响应；插件继续使用公开字段白名单。

## 重跑真实模型测评

```sh
python scripts/evaluate-practice.py --phase dev --run /tmp/mq-campaign/run1 --key-file /path/to/official-deepseek.env
python scripts/evaluate-practice.py --phase test --run /tmp/mq-campaign/run1 --key-file /path/to/official-deepseek.env
```

凭据文件只需包含 DEEPSEEK_API_KEY；使用官方 api.deepseek.com，避免复用插件中的其他提供方密钥。开关与密钥不会写入插件设置。运行会实际消耗 API 配额。

同一 campaign 目录下所有 run 共用 800 次调用、400 万 tokens 的上限。已有请求根据请求签名恢复，只有网络错误会重试一次。预算不足时不启动下一次请求，未知用量保守计入预留量。

恢复要求数据集及运行代码一致。代码变化时应使用新的 run 名称；原始正式运行的源码另存于 runs/formal-v1/code/。要做新一轮完整评测，请使用新的 campaign 父目录，不覆盖已有结果。

```sh
python scripts/evaluate-practice.py --phase report --run evals/mq_v1/runs/formal-v1
```

report 只重算指标，不调用模型。格式修订复验及探索性视觉补测分别保存，不能合并为原始正式成绩。

测试素材作者工具为 scripts/build-mq-eval-fixture.py，需要 Pillow。运行测评本身不需要 Pillow：直接使用已保存并冻结的 PNG。初始标准经过代码/算术断言和内容复核，没有外部人工双标。模型参数和代码哈希在每个正式 run 的 manifest.json 中记录。
