# Socrates

Highlight a passage in your Obsidian notes and question it in a Socratic dialogue. Optional write-back puts an answer into the original note — only after you approve it.

**Desktop only.** This repository is the Obsidian plugin: a thin client. The tutor loop, tools, and file edits run in a **local sidecar** that you start yourself. The sidecar is not published in this repository yet.

## Requirements

- Obsidian desktop 1.5.0 or later (not mobile)
- A local sidecar listening on loopback, default `http://127.0.0.1:8765`
- An API key for an OpenAI-compatible Chat Completions endpoint (set in plugin settings; no environment variables)

Until the sidecar is released, this plugin cannot answer questions on its own. When the sidecar ships, this README will point to the install command.

## Install

Not in the Community plugins directory yet. When it is, use **Settings → Community plugins → Browse**.

Until then, after a GitHub Release exists, copy these files into:

`<Vault>/.obsidian/plugins/socrates-pen/`

- `main.js`
- `manifest.json`
- `styles.css`

Enable the plugin under **Settings → Community plugins**. Restricted mode must be off.

## Use

1. Start the sidecar on this machine (default `127.0.0.1:8765`).
2. **Settings → Socrates**: API key, and optionally Base URL, model, and thinking level.
3. Open a note, select a passage (live preview or reading view).
4. Open the Socrates sidebar and use the current selection, or run the command palette item to do the same.
5. To write an answer back into the note: say where to insert or replace, or use the write-back chip after a real answer. The model must read the file first, then propose an edit. The sidebar asks you to **allow** that edit before anything is saved.
6. **Roll back / Redo** restore the whole note from the sidecar snapshot stack, not just the last selection.

## Privacy and network

- The plugin talks to the sidecar URL you configure (default `http://127.0.0.1:8765`). It does not phone home.
- Model calls leave your machine from the sidecar, to the endpoint you set (default is a public Chat Completions API).
- The API key is stored in this vault at `.obsidian/plugins/socrates-pen/data.json`. If the vault is in Sync, iCloud, or git, the key goes with it.
- Write-back changes the note on disk through the sidecar, after you approve the edit.

## License

MIT. See [LICENSE](LICENSE).

---

# 苏格拉底

在 Obsidian 笔记里划一段，用苏格拉底方式追问。可选把解答写回原文——必须你在侧栏点允许之后才落盘。

**仅桌面。** 本仓库是插件：薄客户端。对话循环、工具和改文件都在你本机启动的 **sidecar** 里跑。sidecar **尚未**放进本仓库。

## 需要

- Obsidian 桌面版 1.5.0 或更高（不能用手机）
- 本机 sidecar，默认监听 `http://127.0.0.1:8765`
- 兼容 OpenAI Chat Completions 的 API Key（在插件设置里填，不用配环境变量）

sidecar 公开之前，只装这个插件问不了问题。sidecar 发布后，本 README 会补上安装命令。

## 安装

还没进社区插件目录。进了之后走 **设置 → 社区插件 → 浏览**。

在此之前，等 GitHub Release 有产物了，把下面三个文件拷进：

`<库>/.obsidian/plugins/socrates-pen/`

- `main.js`
- `manifest.json`
- `styles.css`

在 **设置 → 社区插件** 里启用。需要关掉 Restricted mode。

## 用法

1. 本机先起 sidecar（默认 `127.0.0.1:8765`）。
2. **设置 → Socrates**：填 API Key；需要的话再改 Base URL、模型、Thinking。
3. 打开一篇笔记，划一段（实时预览或阅读模式都行）。
4. 打开苏格拉底侧栏，用当前选区；或用命令面板做同样的事。
5. 要把解答写进原文：说清楚插哪/换哪，或在有真正解答之后用写回。模型必须先读文件，再单独提一次编辑。侧栏弹出审批，点**允许**才写盘。
6. **回到上一版 / 重做**按 sidecar 的快照栈整篇回退，不是只撤选区。

## 隐私与网络

- 插件只访问你配置的 sidecar 地址（默认 `http://127.0.0.1:8765`），不向作者汇报。
- 调模型是 sidecar 按你填的节点发出去的（默认是公开的 Chat Completions 接口）。
- API Key 存在本库 `.obsidian/plugins/socrates-pen/data.json`。库若进了 Sync / iCloud / git，钥匙会跟着走。
- 写回经 sidecar 改磁盘上的笔记，且必须你先批准。

## 许可

MIT，见 [LICENSE](LICENSE)。
