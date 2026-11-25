"""Separate colliding native footnote runs below ruled tables.

BabelDOC writes individual translated glyphs in independent PDF text objects. When
the source has two notes on a single line, their translations can start inside
one another. Move the second run into the source continuation line only when its
entire destination is free and the paired Chinese form is byte-for-byte verified.
"""
from __future__ import annotations

import logging
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz
from output_documents import open_outputs, replace_outputs, translated_page

logger = logging.getLogger(__name__)
NUMBER = rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
GLYPH = re.compile(
    rb"\bBT\s*/[^\s/]+\s+(?P<size>" + NUMBER + rb")\s+Tf\s+"
    rb"(?P<x>" + NUMBER + rb")\s+(?P<y>" + NUMBER + rb")\s+TD\s*"
    rb"(?:<(?P<cid>[a-fA-F0-9]+)>|\((?:\\.|[^()\\])*\))\s*Tj\s+ET\b"
)


@dataclass(frozen=True)
class Glyph:
    code: int
    gid: int
    x: float
    y: float
    box: tuple
    size: float
    seq: int


def _glyphs(page):
    return [
        Glyph(code, gid, *origin, box, span["size"], span["seqno"])
        for span in page.get_texttrace()
        if span["type"] == 0 and span["dir"] == (1, 0)
        for code, gid, origin, box in span["chars"]
    ]


def _table_rules(page):
    rules = set()
    for path in page.get_drawings():
        r = path["rect"]
        if r.height > 1.4 or r.width < page.rect.width * .16:
            continue
        if any(item[0] in ("l", "re") for item in path["items"]):
            rules.add((r.x0, r.x1, r.y1))
    # A table's lower rule has an earlier parallel rule defining the same columns.
    return [(left, right, y) for left, right, y in sorted(rules)
            if any(abs(a-left) < 2 and abs(b-right) < 2 and 5 < y-top < page.rect.height*.8
                   for a, b, top in rules)]


def _letters(chars):
    return sum(chr(g.code).isalpha() for g in chars if 0 <= g.code <= 0x10ffff)


def _bounds(chars):
    return fitz.Rect(min(g.box[0] for g in chars), min(g.box[1] for g in chars),
                     max(g.box[2] for g in chars), max(g.box[3] for g in chars))


def _plans(source, target):
    rules = _table_rules(source)
    if not rules:
        return []
    source_chars, chars = _glyphs(source), _glyphs(target)
    plans = []
    for left, right, rule_y in rules:
        source_note = [g for g in source_chars if left-1 < g.x < right
                       and 1 < g.y-rule_y < 25 and 4 <= g.size <= 11]
        if not source_note:
            continue
        baseline = min(g.y for g in source_note)
        first = [g for g in source_note if abs(g.y-baseline) < .35]
        if _letters(first) < 6 or abs(first[0].x-left) > first[0].size:
            continue
        font_size = first[0].size
        continuation = [g for g in source_note if .9*font_size <= g.y-baseline <= 1.85*font_size]
        if _letters(continuation) < 4 or abs(continuation[0].x-left) > font_size:
            continue
        row = [g for g in chars if left-1 <= g.x < right and abs(g.y-baseline) <= .6
               and abs(g.size-font_size) < max(1.2, font_size*.22)]
        # Within a horizontal text run the glyph origins advance left to right.
        splits = [i for i in range(1, len(row)) if row[i].x < row[i-1].x-font_size]
        if len(splits) != 1:
            continue
        first_run, second_run = row[:splits[0]], row[splits[0]:]
        if any(_letters(run) < 4 for run in (first_run, second_run)):
            continue
        a, b = _bounds(first_run), _bounds(second_run)
        # Superscripts, citation marks or formula fragments may use a different
        # baseline. Move only a complete sequence of independent glyph objects.
        start, end = second_run[0].seq, second_run[-1].seq
        if [g.seq for g in second_run] != list(range(start, end+1)):
            continue
        if any(g.seq in (start-1, end+1) and abs(g.y-baseline) < font_size
               and b.x0-font_size < g.x < b.x1+font_size and g not in row for g in chars):
            continue
        if min(a.x1, b.x1)-max(a.x0, b.x0) < 1.5*font_size:
            continue
        # Keep the source continuation's left margin. Increase the original
        # baseline distance only when taller translated glyphs need more leading.
        dx = continuation[0].x - second_run[0].x
        dy = max(continuation[0].y-baseline, a.y1-b.y0+.23*font_size)
        dest = fitz.Rect(b.x0+dx, b.y0+dy, b.x1+dx, b.y1+dy)
        if dest.x0 < left-1 or dest.x1 > right+1 or dest.y1 > target.rect.y1:
            continue
        moved = {g.seq for g in second_run}
        occupied = target.get_bboxlog()
        if any(dest.intersects(fitz.Rect(box)) for seq, (kind, box) in enumerate(occupied)
               if seq not in moved and kind != "ignore-text"):
            continue
        if any(dest.intersects(previous[3]) for previous in plans):
            continue
        plans.append((second_run, dx, dy, dest))
    return plans


def _rewrite(page, stream, plans):
    matrix = page.transformation_matrix
    if page.rotation or (matrix.a, matrix.b, matrix.c, matrix.d) != (1, 0, 0, -1):
        return stream, 0
    lookup = defaultdict(list)
    for m in GLYPH.finditer(stream):
        # Two decimals are used only as an index. Actual coordinates are checked below.
        lookup[round(float(m["x"]), 2)].append(m)
    replacements = {}
    count = 0
    for run, dx, dy, _ in plans:
        matches = []
        for g in run:
            x, y = g.x-matrix.e, matrix.f-g.y
            candidates = [m for k in {round(x+t, 2) for t in (-.001, 0, .001)}
                          for m in lookup.get(k, ())
                          if abs(float(m["x"])-x) <= .001 and abs(float(m["y"])-y) <= .001
                          and abs(float(m["size"])-g.size) <= .001]
            if len(candidates) > 1:
                candidates = [m for m in candidates if m["cid"] and int(m["cid"], 16) == g.gid]
            if len(candidates) != 1:
                break
            matches.append(candidates[0])
        else:
            indexes = [m.start() for m in matches]
            if indexes != sorted(set(indexes)):
                continue
            offsets = {m.start(axis) for m in matches for axis in ("x", "y")}
            if offsets.intersection(replacements):
                continue
            for m in matches:
                for axis, delta in (("x", dx), ("y", -dy)):
                    value = f"{float(m[axis])+delta:.5f}".rstrip("0").rstrip(".")
                    replacements[m.start(axis)] = m.end(axis), value.encode("ascii")
            count += 1
    if not replacements:
        return stream, 0
    chunks, previous = [], 0
    for start, (end, value) in sorted(replacements.items()):
        chunks.extend((stream[previous:start], value))
        previous = end
    return b"".join([*chunks, stream[previous:]]), count


def _right_forms(source, dual):
    width = source.rect.width
    if abs(dual.rect.width-2*width) > .1 or abs(source.rect.height-dual.rect.height) > .1:
        return set()
    objects = dual.get_xobjects()
    roots = {xref for xref, _, parent, box in objects
             if not parent and abs(box[0]-width) < .1 and abs(box[2]-2*width) < .1}
    right, frontier = set(roots), roots
    while frontier:
        frontier = {xref for xref, _, parent, _ in objects if parent in frontier} - right
        right.update(frontier)
    return right


def _repair(source, mono, dual):
    if any(doc is not None and len(doc) != len(source) for doc in (mono, dual)):
        return 0
    mono_refs = Counter(x for p in mono for x in p.get_contents()) if mono is not None else {}
    dual_refs = Counter(x for p in dual for x, _, _, _ in p.get_xobjects()) if dual is not None else {}
    count = 0
    for i, src in enumerate(source):
        if src.rotation:
            continue
        forms = {}
        if dual is not None:
            forms = {x: dual.xref_stream(x) for x in _right_forms(src,dual[i]) if dual_refs[x] == 1}
            if not forms:
                continue
        with translated_page(mono, dual, i) as out:
            if out.rotation or src.rect != out.rect:
                continue
            changes = _plans(src, out)
            if not changes:
                continue
            if mono is None:
                # All candidate streams belong exclusively to the right form.
                for x,raw in forms.items():
                    fixed, n = _rewrite(out, raw, changes)
                    if n:
                        dual.update_stream(x, fixed)
                        count += n
                continue
            for x in out.get_contents():
                if mono_refs[x] != 1:
                    continue
                original = mono.xref_stream(x)
                matches = [ref for ref, raw in forms.items() if original == raw]
                if dual is not None and len(matches) != 1:
                    continue
                fixed, n = _rewrite(out, original, changes)
                if n:
                    mono.update_stream(x, fixed)
                    if dual is not None:
                        dual.update_stream(matches[0], fixed)
                    count += n
    return count


def repair_table_footnotes(source: Path, mono: Path | None, dual: Path | None) -> int:
    """Move colliding notes only within selected Chinese content."""
    with tempfile.TemporaryDirectory(dir=Path(mono or dual).parent, prefix=".footnotes-") as temp:
        work = Path(temp)
        try:
            with fitz.open(source) as original, open_outputs(mono, dual) as (one, two):
                count = _repair(original, one, two)
                if not count:
                    return 0
                if one is not None:
                    one.save(work / "mono.pdf")
                if two is not None:
                    two.save(work / "dual.pdf")
        except (RuntimeError, ValueError, OSError) as exc:
            logger.warning("保留原排版。表下注释检查遇到 %s", exc)
            return 0
        replace_outputs(work, mono, dual)
        return count
