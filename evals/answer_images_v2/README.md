# 作答图片与递进 rubric 测评

范围：用户上传**答案图片**，适用于解答题和填空题。不是识别题目图表，不测图片选择题。新题只存在于预先编写的测试教材；产品仍只导入已有 meta question。

## 固定输入

- `book.md`：一题开发题（webhook）和一题未放入示范的测试题（配置发布）。
- `gold.json`：模型调用前写好的评分标准。
- `answers.json`：测试题基础、进阶、完整和错误/注入四类答案。
- `assets/`：这些答案的截图、填空作答截图和不可辨认的噪声图。
- `manifest.json`：测前冻结的文件 SHA-256、分组、预算和门槛。
- `examples.json`：真实放进模型上下文的 10 个面试示范；不是测试题。

## 运行闭环

```sh
# prepare 只用于首次创建；已有 manifest 时拒绝覆盖。
python scripts/evaluate-answer-images.py prepare
python scripts/evaluate-answer-images.py dev
# 先检查开发题生成的逐项分值与等级，开发通过后再测冻结测试题。
python scripts/evaluate-answer-images.py test
python scripts/evaluate-answer-images.py report
```

真实请求保存在 `../mq_v1/runs/answer-images-v2/`。复用原 800 次调用上限的剩余 19 次，未扩大预算。缓存按请求及代码快照锁定；相同请求重放不会调用 API，改变已冻结输入会报错。报告阶段完全离线。配置使用显式 key 文件，不在产物中保存密钥。

一份生成 rubric 固定用于所有对应评分；同题文字与图片只改变作答模态，不改变题目、答案键、rubric 或评分输出格式。错误作答、提示注入和不可辨认图片都计入结果。默认门槛：归一化平均误差 ≤ 0.10、配对文字/图片分差 ≤ 0.10、错误作答 0 分、无法辨认必须拒评。

这些样本是清晰截图，**不是实际学生手写照片**。独立测试题数量仍然很小，gold 由任务作者预写、没有外部双标。结果是功能与初步质量验证，不能外推为真实面试评分准确率。
