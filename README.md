<div align="center">

```
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
```

</div>

# Socrates

Highlight a passage in your Obsidian notes and question it in a Socratic dialogue. Optional write-back puts an answer into the original note — only after you approve it.

**Desktop only.** The plugin starts a local tutor process for you (loopback only). You need Python 3.11+ already installed on this computer; the plugin creates an isolated environment under `~/.socrates-pen` and starts it. No terminal.

## Requirements

- Obsidian desktop 1.5.0 or later (not mobile)
- Python 3.11 or later on this machine ([python.org](https://www.python.org/downloads/))
- An API key for an OpenAI-compatible Chat Completions endpoint (Settings → Socrates)

## Install the plugin

When it is in the Community plugins directory: **Settings → Community plugins → Browse → Socrates**.

Until then, download these three files from the [latest GitHub Release](https://github.com/xesws/socrates-pen/releases) and put them in:

`<Vault>/.obsidian/plugins/socrates-pen/`

- `main.js`
- `manifest.json`
- `styles.css`

Enable the plugin under **Settings → Community plugins**. Restricted mode must be off.

On first launch the plugin may take a minute: it creates `~/.socrates-pen/venv` and pip-installs the tutor from this GitHub repository (GitHub + PyPI). After that, **Settings → Socrates** shows whether the local service is running. Fill in your API key there.

## Use

1. Enable the plugin. Wait until Settings → Socrates says the local service is running (or click **Start**).
2. **Settings → Socrates**: API key, and optionally Base URL, model, and thinking level.
3. Open a note, select a passage (live preview or reading view).
4. Open the Socrates sidebar and use the current selection, or run the command palette item to do the same.
5. To write an answer back into the note: say where to insert or replace, or use the write-back chip after a real answer. The model must read the file first, then propose an edit. The sidebar asks you to **allow** that edit before anything is saved.
6. **Roll back / Redo** restore the whole note from the snapshot stack, not just the last selection.

## Privacy and network

- The plugin talks to `http://127.0.0.1:8765` by default. It does not phone home.
- First-time setup downloads the tutor and its Python dependencies from GitHub and PyPI into `~/.socrates-pen`.
- Model calls leave your machine from that local process, to the endpoint you set.
- The API key is stored in this vault at `.obsidian/plugins/socrates-pen/data.json`. If the vault is in Sync, iCloud, or git, the key goes with it.
- Write-back changes the note on disk, after you approve the edit.

## License

MIT. See [LICENSE](LICENSE).

## Development

```bash
npm install
npm test
npm run build
```

For a live vault:

```bash
export VAULT_PLUGIN_DIR=/path/to/vault/.obsidian/plugins/socrates-pen
npm run dev
```

`npm run dev` refuses to start without `VAULT_PLUGIN_DIR`.

---

# 苏格拉底

在 Obsidian 笔记里划一段，用苏格拉底方式追问。可选把解答写回原文——必须你在侧栏点允许之后才落盘。

**仅桌面。** 插件会自己在本机拉起服务（只绑 loopback）。电脑上需要已经装好 Python 3.11+；插件在 `~/.socrates-pen` 里建隔离环境并启动。不用开终端。

## 需要

- Obsidian 桌面版 1.5.0 或更高（不能用手机）
- 本机 Python 3.11 或更高（[python.org](https://www.python.org/downloads/)）
- 兼容 OpenAI Chat Completions 的 API Key（设置 → Socrates）

## 安装插件

进了社区插件目录之后：**设置 → 社区插件 → 浏览 → Socrates**。

在此之前，从 [GitHub Release](https://github.com/xesws/socrates-pen/releases) 下载这三个文件，拷进：

`<库>/.obsidian/plugins/socrates-pen/`

- `main.js`
- `manifest.json`
- `styles.css`

在 **设置 → 社区插件** 里启用。需要关掉 Restricted mode。

第一次启用可能要一分钟：插件会建 `~/.socrates-pen/venv`，并从本 GitHub 仓库 pip 安装（访问 GitHub 和 PyPI）。之后在 **设置 → Socrates** 最上面看本机服务是否在跑，并填 API Key。

## 用法

1. 启用插件。等到设置页显示本机服务在运行（或点 **启动**）。
2. **设置 → Socrates**：填 API Key；需要的话再改 Base URL、模型、Thinking。
3. 打开一篇笔记，划一段（实时预览或阅读模式都行）。
4. 打开苏格拉底侧栏，用当前选区；或用命令面板做同样的事。
5. 要把解答写进原文：说清楚插哪/换哪，或在有真正解答之后用写回。模型必须先读文件，再单独提一次编辑。侧栏弹出审批，点**允许**才写盘。
6. **回到上一版 / 重做**按快照栈整篇回退，不是只撤选区。

## 隐私与网络

- 插件默认只访问 `http://127.0.0.1:8765`，不向作者汇报。
- 第一次安装会从 GitHub 和 PyPI 把脑子和 Python 依赖下到 `~/.socrates-pen`。
- 调模型由这个本机进程按你填的节点发出去。
- API Key 存在本库 `.obsidian/plugins/socrates-pen/data.json`。库若进了 Sync / iCloud / git，钥匙会跟着走。
- 写回改磁盘上的笔记，且必须你先批准。

## 许可

MIT，见 [LICENSE](LICENSE)。

## 开发

```bash
npm install
npm test
npm run build
```

对着一个库热更新：

```bash
export VAULT_PLUGIN_DIR=/path/to/vault/.obsidian/plugins/socrates-pen
npm run dev
```

没设 `VAULT_PLUGIN_DIR` 时 `npm run dev` 会直接退出。
