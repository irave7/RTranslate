"""Headless research-document translation and revision shared by both RTranslate entry points."""
import asyncio
from contextlib import nullcontext
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading

import pymupdf

SOURCE = Path(__file__).resolve().parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from task_progress import JobProgress

PDF_LOCK = threading.Lock()


def pdf_from_text(path, dest):
    data = path.read_bytes()
    codec = "utf-16" if data.startswith((b"\xfe\xff",b"\xff\xfe")) else "utf-8-sig"
    try:
        text = data.decode(codec)
    except UnicodeError as exc:
        raise ValueError("请使用 UTF-8 或带字节顺序标记的 UTF-16 文本。") from exc
    if not text.strip():
        raise ValueError("输入文本为空。")
    with pymupdf.open() as doc:
        page = None
        lines = 0
        for original in text.expandtabs(4).splitlines():
            # Wrap plain text once when creating its source page. PDF input bypasses this step.
            font = "helv" if original.isascii() else "china-s"
            wrapped, line, width = [], "", 0
            for word in re.findall(r"\S+\s*|\s+", original):
                size = pymupdf.get_text_length(word, fontname=font, fontsize=10)
                if line and width + size > 510:
                    wrapped.append(line.rstrip())
                    line, width = "", 0
                for character in word:
                    advance = pymupdf.get_text_length(character, fontname=font, fontsize=10)
                    if line and width + advance > 510:
                        wrapped.append(line.rstrip())
                        line, width = "", 0
                    line += character
                    width += advance
            wrapped.append(line.rstrip())
            for line in wrapped:
                if lines % 46 == 0:
                    page = doc.new_page(width=595,height=842)
                    if len(doc)>2500:
                        raise ValueError("文本过长，请分部分处理。")
                if line:
                    page.insert_text((40,53+(lines%46)*16),line,fontsize=10,fontname=font)
                lines += 1
        doc.save(dest, garbage=3,deflate=True)


def restore_scan_originals(original, dual, scans):
    """Preserve original images and safe, clickable references on the left of a dual PDF."""
    pending = dual.with_name("originals-dual.tmp.pdf")
    scans = set(scans)
    with PDF_LOCK, pymupdf.open(original) as src, pymupdf.open(dual) as translated, pymupdf.open() as rebuilt:
        result = rebuilt if scans else translated
        if len(src) != len(translated):
            raise ValueError("双语译文页数与原稿不匹配。")
        geometry = []
        for i, page in enumerate(translated):
            w, h = page.rect.width, page.rect.height
            left = pymupdf.Rect(0, 0, w / 2, h)
            orig = src[i]
            same_size = abs(orig.rect.width - left.width) < 1 and abs(orig.rect.height - h) < 1
            geometry.append(same_size and orig.rotation == 0 and page.rotation == 0)
            if i+1 in scans:
                merged = result.new_page(width=w, height=h)
                merged.show_pdf_page(left, src, i)
                right = pymupdf.Rect(w / 2, 0, w, h)
                merged.show_pdf_page(right, translated, i, clip=right)
            elif scans:
                result.insert_pdf(translated, from_page=i, to_page=i)
        changed = bool(scans)
        for i, page in enumerate(src):
            if not geometry[i]:
                continue
            out = result[i]
            existing = {tuple(round(value,1) for value in item["from"]) for item in out.get_links()}
            for link in page.get_links():
                area = link["from"]
                if tuple(round(value,1) for value in area) in existing:
                    continue
                kind = link["kind"]
                if kind == pymupdf.LINK_URI and link.get("uri","").lower().startswith(("http://","https://","mailto:")):
                    target = dict(kind=kind,uri=link["uri"])
                elif kind in (pymupdf.LINK_GOTO,pymupdf.LINK_NAMED):
                    to_page = link.get("page",-1)
                    pt = link.get("to")
                    if (not isinstance(to_page, int) or not 0 <= to_page < len(src)
                            or not geometry[to_page] or not isinstance(pt, pymupdf.Point)):
                        continue
                    if kind == pymupdf.LINK_NAMED:
                        pt = pt * src[to_page].transformation_matrix
                    target = dict(kind=pymupdf.LINK_GOTO,page=to_page,to=pt,zoom=link.get("zoom",0))
                else:
                    continue
                out.insert_link({**target,"from":area})
                changed = True
        if not changed:
            return
        if scans:
            result.set_metadata(translated.metadata)
            bookmarks = translated.get_toc()
            if bookmarks:
                result.set_toc(bookmarks)
        result.save(pending,garbage=3,deflate=True)
    os.replace(pending,dual)


@dataclass
class Job:
    ident: str
    folder: Path
    config: dict
    status: str = "awaiting"
    detail: str = "正在读取文献"
    timing: JobProgress = field(default_factory=JobProgress)
    pages: int = 0
    mono_pages: int = 0
    dual_pages: int = 0
    mono: Path | None = None
    dual: Path | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    engine_source: Path | None = None
    scans: list = field(default_factory=list)
    review_store: Path | None = None
    revision: int = 0
    revision_error: str = ""
    figure_labels: list = field(default_factory=list)
    rendered_labels: int = 0


class DocumentEngine:
    def __init__(self):
        self.lock = threading.RLock()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def _progress(self, job, detail, phase=None, fraction=0, count=0, total=0):
        with self.lock:
            job.detail = detail
            if phase:
                job.timing.update(phase, fraction, count, total)

    def _finish_outputs(self, job, source, mono, dual):
        if job.figure_labels:
            from figure_labels import render
            with PDF_LOCK:
                job.rendered_labels = render(source, mono, dual, job.figure_labels)
        if dual is not None:
            restore_scan_originals(source, dual, job.scans)
        with PDF_LOCK:
            for pdf in (mono, dual):
                if pdf is not None:
                    with pymupdf.open(pdf) as doc:
                        if len(doc) != job.pages:
                            raise ValueError("译本页数与原文不符。")
        job.mono, job.dual = mono, dual
        job.mono_pages = job.pages if mono else 0
        job.dual_pages = job.pages if dual else 0
        job.review_store = (mono or dual).parent / "segments.json"

    def translate(self, job, config):
        from figure_preflight import FigurePreflight
        from figure_translation import translate_labels
        from job import translate
        from scanned import prepare_scanned

        def check():
            if job.cancel.is_set():
                raise InterruptedError

        def stage(*args):
            self._progress(job, *args)

        source = job.folder / "paper.pdf"
        try:
            if config["format"] == "txt":
                with PDF_LOCK:
                    pdf_from_text(job.folder / "input.txt", source)
            with pymupdf.open(source) as doc:
                if not doc.is_pdf or doc.needs_pass or not len(doc):
                    raise ValueError("请选择可直接打开的非空 PDF。")
                job.pages = len(doc)
            stage("正在检查英文扫描页面", "scan")
            prepared, job.scans = prepare_scanned(source, job.folder / "ocr-source.pdf",
                lambda msg, pct: stage(msg, "scan", pct / 100), job.cancel)
            job.engine_source = Path(prepared)
            if callback := config.get("agent_source_prepared"):
                callback(job.engine_source)
            figures = config["format"] == "pdf" and config["figureLabels"]
            probe = FigurePreflight(source, job.folder, check, lock=PDF_LOCK) if figures else nullcontext()
            with probe as scan:
                mono, dual = asyncio.run(translate(job.engine_source, job.folder / "output", config["endpoint"],
                    config["key"], config["model"], config["glossary"], lambda msg, pct: stage(msg),
                    job.cancel, stage_progress=stage, direct_api=True, review=True, scan_checked=True,
                    agent_auto_terms=config.get("agent_auto_terms"), agent_min_text_length=config.get("agent_min_text_length"),
                    output_mode=config.get("output", "both")))
                if scan is not None:
                    labels = scan.result(lambda msg: stage(msg), on_progress=lambda n,total:
                        stage("正在识别图内文字", "figure_read", n/max(total,1), n,total))
                    check()
                    if labels:
                        job.figure_labels = translate_labels(labels, config["endpoint"], config["model"], config["key"],
                            config["glossary"], check, lambda msg: stage(msg), on_progress=lambda n,total:
                            stage("正在翻译图内文字", "figure_text", n/max(total,1), n,total))
            check()
            stage("正在写回并核对译本", "publish", .5)
            self._finish_outputs(job, source, mono, dual)
            job.status, job.detail = "done", "译本已生成。"
        except (asyncio.CancelledError, InterruptedError):
            job.status, job.detail = "cancelled", "已取消翻译。"
        except Exception as exc:
            detail = str(exc) or "未能生成译本。"
            if key := config.get("key"):
                detail = detail.replace(key, "•••")
            job.status = "cancelled" if job.cancel.is_set() else "error"
            job.detail = detail[:1600]
        finally:
            with self.lock:
                job.timing.finish(job.status == "done")

    def revise(self, job, rows):
        from job import translate
        from review import restore_running_citations
        try:
            with tempfile.TemporaryDirectory(dir=job.folder) as tmp:
                manifest = Path(tmp) / "segments.json"
                restore_running_citations(rows, job.engine_source)
                manifest.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                mono, dual = asyncio.run(translate(job.engine_source, job.folder / "output", "http://127.0.0.1:1/v1",
                    "", "rtranslate-revision", "", lambda msg, pct: self._progress(job,msg), job.cancel,
                    replay=manifest, local=True, output_mode=job.config.get("output", "both"),
                    agent_min_text_length=job.config.get("agent_min_text_length")))
                self._finish_outputs(job, job.folder / "paper.pdf", mono, dual)
                os.replace(manifest, job.review_store)
                job.revision += 1
                job.revision_error = ""
        except (InterruptedError, asyncio.CancelledError):
            job.revision_error = "已取消本次重排。"
        except Exception as exc:
            job.revision_error = str(exc)[:600] or "无法重新排版。"
        finally:
            job.status = "done"
            job.timing.finish(not job.revision_error)
