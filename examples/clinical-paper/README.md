# 14 页临床论文示例

[下载本次测试的原始 PDF](jcm-10-03647.pdf)。

**Effect of Early Peripheral Parenteral Nutrition Support in an Enhanced Recovery Program for Colorectal Cancer Surgery: A Randomized Open Trial.** Luis Sánchez-Guillén, Leticia Soriano-Irigaray, Francisco López-Rodríguez-Arias, Xavier Barber, Ana Murcia, M José Alcaide, Verónica Aranaz-Ostáriz, Álvaro Soler-Silva, Andrés Navarro-Ruiz, Antonio Arroyo. *Journal of Clinical Medicine*, 2021, 10(16), 3647. [出版社原文及版本信息](https://www.mdpi.com/2077-0383/10/16/3647) · [DOI](https://doi.org/10.3390/jcm10163647)

© 2021 作者。原文按 [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/) 许可分发。本目录中的英文 PDF 仅改文件名，内容保持原样。原文 PDF 的 SHA-256 为 `d01ad116d8c3c716ed8cc7f52c156380464e894ee98ad21393cd579de181deb8`。论文及其内容适用论文自身许可。

## 验证记录

在同篇论文的 169 个真实请求和 126 个已记录批次上，以相同的 o200k_base 分词器测量传给宿主的 JSON。原生请求 messages 合计 163,305 个输入 token，本译的紧凑批次为 44,986，原译去除重复提示、无关术语和不可读标题后为 26,907。原译较前一版减少 40.2%，较原生消息减少 83.5%。此处计量待译数据，不计宿主自带系统指令、聊天记录、推理和译文输出。严格隔离会话另有每次启动宿主的固定开销。使用本机已登录的 Codex CLI 0.165.0 建立两个不同的临时会话，服务记录的总输入分别为 6,258 和 6,260 token。原先使用完整缺省编码指令的可比测试为 11,534 和 11,540。两次运行均为短批次，不能把这组数直接当作整篇账单。

同一篇论文的历史完整初译在 Apple M1 Max 64 GB 上耗时 4 分 45 秒（Codex 工作者并行）。此前独立 DeepSeek API 的 Flash 用时 2 分 12 秒、约 ¥0.29026；Pro 用时 3 分 06 秒、约 ¥1.01567。三次设置及服务商不同，Flash 整篇分两段完成。这些费用来自已公开测试当时的账单。原译本次的数据压缩回放没有重复向 DeepSeek 付费，以上时间不代表新版隔离会话的实测整篇速度。[历史 API 核对及明细](https://github.com/irave7/LocalLLMTranslate/blob/v1.0.0/examples/clinical-paper/benchmarks.md)

验收覆盖第 5 页流程图、第 8 页表 3 脚注与第 10 页决策树；表 3–5 已逐格核对 235 个数值。兼容性复测分别从这篇论文的已校订译段执行中文和双语原生重排；每种模式都只产生所选译本，图中 40 处中文标签保留，第 8 页两条脚注的基线间距为 10.43 pt。第 9 页表 4 两条长行标题仍较挤。论文供软件测试与对照阅读。

[下载英中术语表](glossary.txt)
