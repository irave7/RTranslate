# 原译的源代码与运行组件

原译的可安装包附带 Python 源码。仓库根目录的 LICENSE 为 GNU Affero General Public License v3。随包的 PDFMathTranslate-next 源码和 RetainPDF 派生模块保留了各自的许可证。压缩包里的 `engine/upstream_runtime/engine-*.lock` 记录对应平台实际使用的 Python 依赖版本和发行文件哈希。

| 组件 | 在原译中的用途与来源 |
| --- | --- |
| PDFMathTranslate-next | 科研论文段落翻译与排版管线，AGPL v3。上游原版与修改记录见 `upstream_ui/UPSTREAM.md`，许可全文见 `upstream_ui/LICENSE`。[上游提交](https://github.com/PDFMathTranslate/PDFMathTranslate-next/tree/07022b4d8585330c6b8413dd2cea9d203d1efb36) |
| RetainPDF | 用于句子、上下文和标记处理的衍生模块。上游 MIT 许可与版权声明见 `upstream_ui/retainpdf_support/LICENSE`。[上游提交](https://github.com/wxyhgk/retain-pdf/tree/47fc4de6c5afc4d05b71a9cfd7bc95a918ad2c86) |
| BabelDOC 0.7.1 | 首次运行时从 PyPI 获取 AGPL v3 排版引擎。原译对私有环境中的 `babeldoc/const.py` 应用一项缓存目录补丁，见 `upstream_runtime/__init__.py`。[原版源代码](https://github.com/funstory-ai/BabelDOC/tree/v0.7.1) · [原版 Python 发行版](https://pypi.org/project/BabelDOC/0.7.1/) |
| PyMuPDF / MuPDF | PDF 读取、写入和扫描页文字识别。私有 Python 环境采用锁定的 AGPL 发行文件，保留其许可。[PyMuPDF 项目](https://github.com/pymupdf/PyMuPDF) · [MuPDF 源码](https://github.com/ArtifexSoftware/mupdf) |
| uv / CPython | 启动脚本从官方分发源获取已固定的 uv 或 CPython。uv 提供 MIT / Apache-2.0 双许可，Python 的许可证随运行环境分发。[uv](https://github.com/astral-sh/uv) · [CPython 许可](https://docs.python.org/3/license.html) |
| 排版资源与英语 OCR | BabelDOC 使用布局分析模型、字符映射和字体渲染文档。首次下载的资源遵循相应上游条款。英文 OCR 使用 `tessdata_fast` 的 Apache-2.0 数据。[版面模型](https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx) · [BabelDOC 字体与映射资源](https://github.com/funstory-ai/BabelDOC-Assets) · [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) |
| Microsoft Visual C++ Redistributable | Windows 缺少原生运行库时，原译从 Microsoft 下载并校验官方安装程序，其许可和授权步骤由 Microsoft 提供。[微软说明](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist) |
