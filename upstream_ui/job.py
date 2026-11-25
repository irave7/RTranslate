"""Translate one desktop PDF with the official PDFMathTranslate-next/BabelDOC pipeline."""
import asyncio
from contextlib import aclosing, suppress
import csv
import os
from pathlib import Path
import sys
import tempfile
import threading
from typing import Callable
import uuid

SOURCE = Path(__file__).resolve().parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from pdf2zh_next.config.model import SettingsModel
from pdf2zh_next.config.translate_engine_model import FreeLLMAPISettings
from pdf2zh_next.high_level import do_translate_async_stream


STAGE_LABELS = {
    "Parse PDF and Create Intermediate Representation": "正在解析 PDF",
    "DetectScannedFile": "正在识别扫描页面",
    "Parse Page Layout": "正在识别页面布局",
    "Parse Table": "正在识别表格",
    "Parse Paragraphs": "正在识别段落",
    "Parse Formulas and Styles": "正在识别公式与样式",
    "Automatic Term Extraction": "正在提取专业术语",
    "Translate Paragraphs": "正在按段落翻译正文",
    "Typesetting": "正在排版译文",
    "Add Fonts": "正在嵌入字体",
    "Generate drawing instructions": "正在绘制页面",
    "Subset font": "正在处理输出字体",
    "Save PDF": "正在保存 PDF",
}

ACADEMIC_STYLE = (
    "将科研论文译成准确、通顺的简体中文，遵循给定术语表。按整段理解跨行长句，"
    "结合上下文确定专业术语的含义。保留原文的因果、否定、比较方向与不确定性。"
    "所有例数、百分比、单位、统计量、缩写、引文编号、公式占位符均应与原文对应；"
    "逐字核对数字。不推测或补充原文未给的数值、置信水平或量表分级；"
    "如果原文只写 CI，就只写 CI。保持输入项、标签、占位符及指定的输出格式。"
)


def write_glossary(value: str, path: Path):
    entries = []
    for index, line in enumerate(value.splitlines(), 1):
        if not line.strip():
            continue
        english, sep, chinese = line.partition("=")
        if not sep or not english.strip() or not chinese.strip():
            raise ValueError(f"术语表第 {index} 行请使用「英文 = 中文」。")
        entries.append((english.strip(), chinese.strip()))
    if entries:
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("source", "target"))
            writer.writerows(entries)
        return str(path)
    return None


async def translate(source: Path, output_dir: Path, base_url: str, token: str, model: str,
                    glossary: str, progress: Callable[[str, float], None],
                    cancelled: threading.Event, *, local: bool = False, review: bool = False,
                    replay: Path | None = None,
                    scan_checked: bool = False, stage_progress=None, direct_api: bool = False,
                    agent_auto_terms: bool | None = None,
                    agent_min_text_length: int | None = None,
                    output_mode: str = "both") -> tuple[Path | None, Path | None]:
    """Return the requested native PDFs. An unrequested output has no path."""
    if output_mode not in ("mono", "dual", "both"):
        raise ValueError("无效的 PDF 输出类型。")
    if cancelled.is_set():
        raise asyncio.CancelledError()
    source, output_dir = Path(source).resolve(), Path(output_dir).resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ValueError("请选择可读取的 PDF 文件。")
    output_dir.mkdir(parents=True, exist_ok=True)
    provider = FreeLLMAPISettings(freellmapi_base_url=base_url, freellmapi_api_key=token,
                                  freellmapi_model=model or "auto")
    provider._direct_api = direct_api
    provider._direct_local = local
    provider._user_terms = glossary
    provider.validate_settings()
    with tempfile.TemporaryDirectory(prefix=".next-", dir=output_dir) as folder:
        work = Path(folder)
        settings = SettingsModel(translate_engine_settings=provider)
        settings.pdf.no_mono = output_mode == "dual"
        settings.pdf.no_dual = output_mode == "mono"
        # The threshold changes the native batch identity. The agent records
        # it on creation and uses that same value for every offline revision.
        if agent_min_text_length is not None:
            settings.translation.min_text_length = agent_min_text_length
        # The desktop has inspected every page and prepared image-only pages.
        # Native image-difference detection mistakes sparse, typeset pages for scans.
        if scan_checked or replay:
            settings.pdf.skip_scanned_detection = True
        if review:
            settings._review_work = str(work / "native")
        if replay:
            settings._review_replay = str(replay)
            settings.translation.no_auto_extract_glossary = True
        if agent_auto_terms is not None:
            settings.translation.no_auto_extract_glossary = not agent_auto_terms
            # Several native batches can wait for one tool call by the desktop agent.
            settings.translation.pool_max_workers = 12
            settings.translation.term_pool_max_workers = 12
        settings.translation.output = str(work)
        settings.translation.lang_in = "en"
        settings.translation.lang_out = "zh-CN"
        settings.translation.custom_system_prompt = ACADEMIC_STYLE
        settings.translation.glossaries = write_glossary(glossary, work / "terms.csv")
        if local:
            settings.translation.pool_max_workers = 2
            settings.translation.term_pool_max_workers = 2
        # Next shares the actual main translator when the term engine and QPS match.
        settings.translation.term_qps = settings.translation.qps
        # Pages, formulas, table text, bilingual placement and typography use Next defaults.
        service = "本地模型" if local else ("大模型 API" if direct_api else "FreeLLMAPI")
        if agent_auto_terms is not None:
            service = "宿主 Agent"
        progress("正在使用已校订的段落重新排版" if replay else f"正在连接{service}并准备 PDF 排版", 0.0)

        async def render():
            async with aclosing(do_translate_async_stream(settings, source)) as events:
                async for event in events:
                    if event["type"] == "error":
                        raise RuntimeError(event.get("error") or "文献翻译失败。")
                    if event["type"].startswith("progress_"):
                        percent = min(99.9, max(0.0, float(event.get("overall_progress") or 0)))
                        stage = event.get("stage") or "正在翻译与排版"
                        label = STAGE_LABELS.get(stage, stage)
                        if stage_progress:
                            finished = event["type"] == "progress_end"
                            fraction = float(event.get("stage_progress") or 0) / 100
                            text_work = event.get("text_work_fraction")
                            if text_work is not None:
                                # A long abstract takes more inference than a one-cell statistic.
                                fraction = .8 * text_work + .2 * fraction
                            stage_progress(label, stage, 1.0 if finished else fraction,
                                           int(event.get("stage_current") or 0), int(event.get("stage_total") or 0))
                        else:
                            progress(label, percent)
                    if event["type"] == "finish":
                        result = event["translate_result"]
                        paths = (result.mono_pdf_path, result.dual_pdf_path)
                        selected = []
                        for mode, path in zip(("mono", "dual"), paths):
                            if output_mode not in (mode, "both"):
                                selected.append(None)
                            elif path and Path(path).is_file():
                                selected.append(Path(path))
                            else:
                                raise RuntimeError("排版引擎未生成所选 PDF。")
                        return tuple(selected)
            raise RuntimeError("排版引擎在生成 PDF 前已退出。")

        task = asyncio.create_task(render())
        try:
            while not task.done():
                if cancelled.is_set():
                    raise asyncio.CancelledError()
                await asyncio.wait({task}, timeout=.2)
            mono, dual = await task
            if cancelled.is_set():
                raise asyncio.CancelledError()
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        from footnotes import repair_table_footnotes
        await asyncio.to_thread(repair_table_footnotes, source, mono, dual)
        if cancelled.is_set():
            raise asyncio.CancelledError()

        # Commit both files together. A cancelled or failed job publishes neither.
        stage = work / "publish"
        stage.mkdir()
        if review:
            from review import collect_segments
            collect_segments(source, work / "native", stage / "segments.json")
        for output in (mono, dual):
            if output is not None:
                os.replace(output, stage / output.name)
        result_dir = output_dir / ("next-" + uuid.uuid4().hex)
        os.replace(stage, result_dir)
        progress("所选 PDF 已生成", 100.0)
        return tuple(result_dir / path.name if path else None for path in (mono, dual))
