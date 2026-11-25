# RetainPDF 续句规则

rules.py 取自 RetainPDF 的 services/continuation/rules.py，版本 47fc4de6c5afc4d05b71a9cfd7bc95a918ad2c86，许可证见同目录 LICENSE。文件中两条 item_reader 导入改接 FLA Translate 的已分类 PDF 正文块。其余规则来自该提交。[原始代码](https://github.com/wxyhgk/retain-pdf/blob/47fc4de6c5afc4d05b71a9cfd7bc95a918ad2c86/backend/translation/retainpdf_translation/services/continuation/rules.py)

document.py 根据原 PDF 中的文本、页码和边界框查找相邻页和双栏的阅读边界。只有几何及语言规则均支持衔接，且 next 正文片段在原件上唯一匹配时，prompt.py 才将相邻文字作为该 ID 的上下文。原生 BabelDOC 负责公式、样式、段落 ID 和排版。复杂表格、无法定位的片段和没有可选文字的图像需依赖 next 自身流程。
