"""Work on the native document(s) selected for this PDF export."""
from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import shutil

import pymupdf


@contextmanager
def open_outputs(mono, dual):
    with ExitStack() as files:
        yield tuple(files.enter_context(pymupdf.open(path)) if path else None for path in (mono, dual))


@contextmanager
def translated_page(mono, dual, index):
    """Inspect the translated page in its original local coordinates."""
    if mono is not None:
        yield mono[index]
    else:
        page = dual[index]
        right = right_half(page)
        # The clip is a page view for matching original geometry. No single-
        # language PDF is written when the caller requested only bilingual.
        with pymupdf.open() as view:
            projected = view.new_page(width=right.width, height=right.height)
            projected.show_pdf_page(projected.rect, dual, index, clip=right)
            yield projected


def right_half(page):
    rect = page.rect
    return pymupdf.Rect(rect.width / 2, 0, rect.width, rect.height)


def replace_outputs(work, mono, dual):
    """Publish after all edits succeed, rolling back a partially replaced pair."""
    targets = [(Path(path), work / name) for path,name in ((mono,"mono.pdf"),(dual,"dual.pdf")) if path]
    if len(targets) == 1:
        target, pending = targets[0]
        os.replace(pending, target)
        return
    previous = work / "previous.pdf"
    try:
        os.link(targets[0][0], previous)
    except OSError:
        shutil.copyfile(targets[0][0], previous)
    os.replace(targets[0][1],targets[0][0])
    try:
        os.replace(targets[1][1], targets[1][0])
    except BaseException:
        os.replace(previous, targets[0][0])
        raise
