# 独立 API

原译可以在终端或 Agent 中直接连接兼容 OpenAI Chat Completions 的服务。服务商可选 DeepSeek、自建兼容服务或已经部署的 FreeLLMAPI。准备服务的基础地址、模型 ID 和密钥。模型名采用服务商当前账户可用的实际标识。

通过系统环境或你使用的密钥管理器向当前进程提供 `RTRANSLATE_API_KEY`。如密钥已经保存在另一环境变量，使用 `--key-env 变量名`。命令只包含变量名。

以下示例在已安装的技能目录运行。每篇论文使用一个新的任务目录，服务商地址与模型 ID 按你的实际配置替换。

```sh
/bin/sh scripts/rtranslate.sh api "paper.pdf" --job "paper-zh" --base-url "https://api.deepseek.com/v1" --model "你的模型ID" --output mono
```

```powershell
.\scripts\rtranslate.cmd api ".\paper.pdf" --job ".\paper-zh" --base-url "https://api.deepseek.com/v1" --model "你的模型ID" --output mono
```

每次只发送当前待译项和必要上下文，不传已完成的对话或译文。使用 `--stream` 可启用流式响应。任务成功时返回实际输出路径。服务确认返回的答复先保存在本机，再提交校验。中断后使用同一服务、模型和 `--job` 并加 `--resume` 继续，省略输入文件。若超时后无法判断服务商是否已经计费，命令会停下；确认愿意重发后再加 `--retry-uncertain`。实际账单以服务商为准。
