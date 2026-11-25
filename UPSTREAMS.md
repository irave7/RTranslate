# 原译的上游来源

原译从[本译的论文翻译流程](https://github.com/irave7/LocalLLMTranslate)提取了无界面的文档处理部分，新增独立 Agent 工作包、可恢复 API 请求、输出偏好和跨平台技能安装。主要许可证为 AGPL-3.0。技能包内含对应引擎代码及第三方声明。[LICENSE](LICENSE) · [第三方声明](packaging/RTranslate-NOTICES.md)

| 项目 | 版本、用途与出处 |
| --- | --- |
| PDFMathTranslate-next | 取自 2.10.2。保留原生段落、样式与公式处理，接入宿主翻译及校订。源码中保留上游版权信息。[固定提交](https://github.com/PDFMathTranslate/PDFMathTranslate-next/tree/07022b4d8585330c6b8413dd2cea9d203d1efb36) |
| BabelDOC | 0.7.1。负责 PDF 布局识别、字体处理和单语或双语写回；初次运行按平台锁定依赖安装。[上游](https://github.com/funstory-ai/BabelDOC) |
| RetainPDF | 使用固定提交的跨页跨栏续句规则，结合可定位正文几何信息，给当前工作包提供少量邻文。[规则来源与 MIT 许可](upstream_ui/retainpdf_support/UPSTREAM.md) |
| PyMuPDF 与 Tesseract | 读取 PDF、构建英文扫描文字层、复原左侧原稿和图表标注。首次识别获取已固定校验值的英文识别数据。[PyMuPDF](https://github.com/pymupdf/PyMuPDF) · [英文数据](https://github.com/tesseract-ocr/tessdata_fast) |
| FreeLLMAPI | 用户可以将已经运行的兼容网关作为 API 地址填写，认证和模型列表由该服务提供。[上游与服务说明](https://github.com/tashfeenahmed/freellmapi) |

排版依赖、平台锁定版本与哈希可在 upstream_runtime 目录查阅。本地语言模型、GGUF 和推理服务均无需部署。
