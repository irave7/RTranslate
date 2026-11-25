# PDFMathTranslate-next 适配

原译使用 [PDFMathTranslate-next 2.10.2 的固定源码](https://github.com/PDFMathTranslate/PDFMathTranslate-next/tree/07022b4d8585330c6b8413dd2cea9d203d1efb36)。保留的引擎文件按同目录 LICENSE 分发。

document_engine.py 调度 TXT 转换、扫描识别、原生译段、图中文字和输出核验。job.py 按本次任务的输出偏好配置 BabelDOC。模型适配器向任务私有的回环队列发送请求，由 Agent 或独立 API 的工作进程消费。retainpdf_support 负责相邻跨页、跨栏片段。改动也包括数值校验、单段校订重排、中文表格脚注处理和双语左侧原稿链接恢复。
