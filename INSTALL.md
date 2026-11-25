# 从 GitHub 安装原译

把项目链接发给当前 Agent 并请它安装。安装器会从同一公开 Release 取得技能包和 SHA-256 清单，校验后写入对应的技能目录。需要电脑能访问 GitHub。首次翻译还需要联网获取固定版本的 Python、PDF 排版库、字体和英文识别资源。

## 给可以运行终端的 Agent

先判断操作系统与当前宿主。Codex 和 DeepSeek Harness 用 `shared`，WorkBuddy 用 `workbuddy`。用 Git 克隆仓库后，在仓库根目录执行以下对应命令。也可从 Release 对应标签下载脚本到本地，阅读后运行。

| 环境 | Codex / DeepSeek Harness | WorkBuddy |
| --- | --- | --- |
| macOS | `/bin/sh install.sh --host shared` | `/bin/sh install.sh --host workbuddy` |
| Windows PowerShell | `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -TargetHost shared` | `powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -TargetHost workbuddy` |

未安装 Git 时，可以用以下命令直接取得 **v1.0.0** 安装脚本。示例安装到 Codex / DeepSeek Harness，WorkBuddy 将 `shared` 改成 `workbuddy`。

macOS 终端：

```sh
installer=$(mktemp -t rtranslate-installer)
curl -fsSL https://raw.githubusercontent.com/irave7/RTranslate/v1.0.0/install.sh -o "$installer" && /bin/sh "$installer" --host shared
```

Windows PowerShell：

```powershell
$installer = Join-Path $env:TEMP "RTranslate-install.ps1"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
(New-Object Net.WebClient).DownloadFile("https://raw.githubusercontent.com/irave7/RTranslate/v1.0.0/install.ps1", $installer)
powershell -NoProfile -ExecutionPolicy Bypass -File $installer -TargetHost shared
```

默认安装到用户的技能目录。Codex 与 DeepSeek Harness 共用 `~/.agents/skills/rtranslate`；WorkBuddy 用 `~/.codebuddy/skills/rtranslate`。只在一个项目中使用时，在上述命令末尾补上 `--project "<项目路径>"`（Mac）或 `-Project "<项目路径>"`（Windows）。在 WorkBuddy 的项目任务中也可使用项目的 `.codebuddy/skills/rtranslate/`。[Codex 技能发现](https://learn.chatgpt.com/docs/build-skills) · [DeepSeek Harness 技能源](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/skills.md) · [WorkBuddy 项目配置](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)

从安装后的目录运行 `scripts/rtranslate.sh preferences` 或 `scripts\rtranslate.cmd preferences`。初次应返回 `{"output":"mono","saved":false}`。要在安装时把 PDF 排版与英文 OCR 一并准备好，可再运行同一启动脚本的 `--prepare`。预热不会调用翻译模型。打开新的宿主会话并确认可以选择「原译」。如果界面只提供上传技能，可下载 `rtranslate.zip` 并在 WorkBuddy 的「技能 → 添加技能 → 上传技能」导入。[下载通用技能包](https://github.com/irave7/RTranslate/releases/latest/download/rtranslate.zip) · [WorkBuddy 技能管理](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)

## 在 Codex 的插件列表中管理

下载 `rtranslate-codex-plugin.zip` 后完整解压，在含 `.agents/plugins/marketplace.json` 的目录执行以下命令。用户级技能目录和本地插件目录选用一种即可。[下载 Codex 插件](https://github.com/irave7/RTranslate/releases/latest/download/rtranslate-codex-plugin.zip)

```sh
codex plugin marketplace add .
codex plugin add rtranslate@rtranslate-local
codex plugin list --marketplace rtranslate-local
```

## 输出与偏好

| 你的要求 | 原译的行为 |
| --- | --- |
| 初次请求翻译 | 提示默认仅中文版，可改为仅中英对照或两份；记住首选 |
| 这次给我双语版 | 本次仅生成中英对照，下一篇回到已存偏好 |
| 以后两份都要 | 将默认设置改成两份 |
| 恢复初始设置 | 清除已存格式，回到仅中文和首次提示 |

开发者可用 `preferences --output mono|dual|both` 保存偏好，或用 `preferences --reset` 恢复。任务创建时固定该任务的格式，续跑和单段校订沿用它。
