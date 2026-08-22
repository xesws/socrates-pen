# 真跑采集的原始记录

这一目录里每一份 JSON 都是**真的跑出来的**：请求体是发出去的那一个，
`events` 是 SSE 流里逐条收到的 JSON 载荷，`answer` 是把 `token` 事件拼起来的全文。

**模型**：`deepseek-v4-flash`（任何 OpenAI 兼容节点都行）
**教材**：[`../从零手写DQN.md`](../从零手写DQN.md)（1405 行）
**选区**：Level 3 第四拍 决策① 「为什么非要两个网络」，第 954–964 行

跑在一个**隔离的 sidecar** 上（自己的 `PEN_HOME`、教材用的是副本、端口 8771），
所以写回和回滚不会碰到仓库里那份原文。

## 唯一一处改动：绝对路径

采集时那个隔离环境在一个临时目录下。落盘之后把它换成了中性路径，
好让别人读起来像自己机器上的样子：

| 原样 | 换成 |
| --- | --- |
| `<临时目录>/vault` | `/Users/you/vault` |
| `<临时目录>/pen-home` | `/Users/you/.socrates-pen` |

**除此之外一个字符都没动**，包括模型的错别字、空行和 markdown 里的怪异之处。

## 目录

| 文件 | 是什么 |
| --- | --- |
| `01-import.json` | `POST /v1/handbooks/import` —— 把教材登记进书架 |
| `02-session.json` | `POST /v1/sessions` |
| `02b-system-prompt.json` | 落盘的 `messages[0]`，v0.15.0「教材无关」的物证 |
| `03-chat-socratic.json` | 芯片 `socratic`：不给答案，反问 |
| `04-chat-explain-zero.json` | 芯片 `explain_zero`：TL;DR →(a)(b)(c)→ 两个能跑的例子 |
| `05-chat-free-three-questions.json` | 芯片 `free`：读者自己打的三个 DQN 问题 |
| `06-deep-inbox.json` | `GET /v1/sessions/{sid}/deep?since=N` 的轮询过程 |
| `06b-deep-ledger.json` | 深挖账本原文，带 `axis` / `depth` / `anchors` / `why` |
| `07-chat-writeback.json` | 芯片 `writeback`：`approval` 事件里的 `old_string` / `new_string` |
| `08-approve-allow.json` | `allow=true` → 落盘，带前后字节数与 md5 |
| `08b-approve-deny.json` | `allow=false` → **磁盘逐字节未变** |
| `09-snapshots-rollback.json` | 快照状态 → 回滚 → **md5 还原成原文** |
| `10-usage.json` | `GET /v1/usage`，上面全部跑完之后的真实账单 |
| `11-session-en.json` | `Accept-Language: en` 建的会话，含完整 system prompt |
| `12-chat-en.json` | 英文界面下的 `socratic` / `explain_zero` |
| `13-deep-en.json` | 英文那两轮深挖：第一轮被闸门全枪毙，第二轮留下一条英文题 |
| `14-chat-free-en.json` | 英文提问版的三问 |
| `15-permission-gates.json` | 直接驱动 `pen/agent/permissions.py`，**不经模型**，可逐条复现 |

## 自己复现

```bash
python -m pen.index --check docs/demo/从零手写DQN.md   # 先确认教材能被索引
```

然后起一个隔离 sidecar（别用你日常那个），把 `docs/demo/从零手写DQN.md`
拷进一个临时 vault，按上表顺序打这些端点即可。
