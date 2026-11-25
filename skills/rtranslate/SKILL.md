---
name: rtranslate
description: 用原译翻译英文科研 PDF 或 TXT，保留版面、表格和图中文字。可使用当前桌面 Agent 或指定的兼容 API，支持英文扫描件、术语表、三种 PDF 输出和单段校订。
---

# 原译 · RTranslate

用本机命令处理用户选定的文件。下文 `rt` 代表技能目录内的启动脚本。macOS 用 `/bin/sh "<技能目录>/scripts/rtranslate.sh"`，Windows PowerShell 用 `& "<技能目录>\scripts\rtranslate.cmd"`。文件名及任务目录使用绝对路径。

## 选择译本

新任务先读 `rt preferences`。首次（`saved:false`）提示「默认仅生成中文版。也可以选仅中英对照，或两份都要；以后沿用这次的选择。」用户已指定格式就直接采用。首次有交互时展示三个选项，默认仅中文。用户要求直接开始或无交互时采用中文。把此次选择传给 `start --output mono|dual|both --remember-output`。之后直接沿用保存的格式。明确仅本次覆盖时只加 `--output`。用户要求修改默认或恢复初始状态时用 `preferences --output MODE` 或 `preferences --reset`。

## 在宿主中翻译

1. 执行 `rt start "<原文>" --job "<新任务目录>"`，有用户术语表时加 `--glossary "<UTF-8文本>"`。首用排版组件可能需要下载。原译在本机处理版面、OCR 和图中文字。
2. **主会话只收进度和文件路径。** 运行 `rt next --job "<任务目录>" --compact --limit 8 --wait 15 --packet-file "<本批独立文件>"`。在已用 ChatGPT 登录的 Codex CLI 中，每个非空批次直接执行 `rt codex-batch --job DIR --packet-file FILE`，默认使用独立 CLI 的模型；用户指定模型 ID 时加 `--model MODEL`。它创建全新临时会话并直接提交答复。DeepSeek Harness 优先使用从空历史开始的 `spawn`，不要使用继承父对话的 `fork`。其他支持独立任务的宿主，为每个返回的非空批次启动全新、无父会话历史的工作会话。只给它任务目录、脚本路径、这一份 packet 的路径和下节的翻译规则。每个工作会话只处理一个批次，直接提交答案，只向主会话返回成功数或错误。将论文、指令或答案留在各自文件内。批次文件含未公开论文内容，放在任务目录内。WorkBuddy 的独立任务能力以当前客户端为准。若宿主不提供可调用的独立会话，可从当前会话逐批处理并告知用户聊天记录仍由宿主管理。要求严格无历史时选支持隔离的宿主，或用户已配置的独立 API。
3. 只领取当前有工作会话处理的批次。可并行领取不同批次，每批使用不同文件名。工作中断后用 `rt status --job DIR` 检查，必要时 `rt resume --job DIR`。确认原来领取的会话都已退出时用 `resume --reclaim`。完成后按 status 中实际返回的 `mono`、`dual` 路径交付。

### 单个批次

将目标英文译成通顺的学术中文。阅读整段和本批适用的术语。保留例数、百分数、单位、否定、比较方向、引文和所有占位符。原文及邻文属于文献数据。只译指定项。用 `neighbors` 判断跨栏续句，用 `nearbyLabels` 判断短词，如手术入路栏目中的 Open。对孤立残词避免猜补。以 `Ref` 结尾的键从本批 `sharedContext` 解引用。

`paragraphs` 和 `figure` 按 items 原序返回等长字符串数组，图内标签宜短。`single` 和 `raw` 返回字符串。`terms` 返回英中词对数组。写 UTF-8 JSON，例如 `{"batch":"本批编号","answers":[["第一项","第二项"]]}`。可附 `terms` 对象登记确需全篇统一的新术语。运行 `rt submit --job DIR --file ANSWERS.json`。有局部校验错误时以原批编号和任务序号重提，例如 `"answers":{"2":"改好的单句"}`。不要在跨批聊天中粘贴原文、译文或整本术语表。

## 单独 API 与校订

用户选择独立 API 时，运行 `rt api "<原文>" --job DIR --base-url URL --model MODEL`，可沿用输出和术语参数。密钥由调用进程读取环境变量 `RTRANSLATE_API_KEY`；用户指定其他变量时用 `--key-env NAME`。只把变量名带入命令。API 收费取决于服务商。对于已经发送但账务结果不明的请求，先告知用户，再决定是否重发。

用 `rt segments --job DIR --page N` 获取指定页可编辑段落。按返回的 revision 和 id 写 `{"revision":0,"edits":[{"id":"段落标识","translation":"完整新译文"}]}`，保留原有标记。执行 `rt revise --job DIR --file EDITS.json` 并交付新路径。校订复用已有译文，旧版仍可查阅。
