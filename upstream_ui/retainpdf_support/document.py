"""Read the physical boundary blocks; never change BabelDOC's paragraph geometry."""
from collections import defaultdict
from dataclasses import dataclass, replace
import re
import unicodedata


PAGE_NUMBER = re.compile(r"^(?:page\s*)?[-–]?\s*\d+(?:\s*(?:of|/)\s*\d+)?\s*[-–]?$", re.I)
LIST_OR_HEADING = re.compile(r"^(?:[•●▪◆◊*]|[-–]\s|\(?\d+(?:\.\d+)*[.)]\s|\d+(?:\.\d+)+\s+[A-Z]|[A-Za-z][.)]\s)")
IGNORED_MARKUP = re.compile(r"<[^>]*>|\{v\d+\}")
ANCHOR_LENGTH = 30


def item_is_bodylike(item):
    return item.get("role") == "body"


def item_is_aggregate_geometry_member(item):
    return False  # The reader supplies native text blocks, never aggregate geometry.


def key(text):
    normalized = unicodedata.normalize("NFKC", IGNORED_MARKUP.sub("", text))
    return "".join(c for c in normalized.casefold() if c.isalnum())


@dataclass(frozen=True)
class Block:
    page: int
    bbox: tuple
    text: str
    body: bool
    height: float
    zone: str = "single_column"

    def payload(self, boundary):
        return dict(page_idx=self.page, bbox=self.bbox, protected_source_text=self.text,
                    layout_zone=self.zone, layout_boundary_role=boundary,
                    role="body" if self.body else "other")


def _blocks(page):
    import pymupdf
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    result = []
    for raw in page.get_text("dict", flags=flags)["blocks"]:
        lines = raw.get("lines", [])
        if not lines:
            continue
        texts = ["".join(s["text"] for s in line["spans"]).strip() for line in lines]
        text = " ".join(s for s in texts if s)
        if not text:
            continue
        box = tuple(raw["bbox"])
        edge = box[3] < .10 * page.rect.height or box[1] > .91 * page.rect.height
        if edge and (PAGE_NUMBER.fullmatch(text) or any(PAGE_NUMBER.fullmatch(t) for t in texts)):
            continue
        # Multiple line fragments sharing a baseline are characteristic of tables
        # (including numeric tables incorrectly grouped as a single "text" block).
        heights = sorted((line["bbox"][1] + line["bbox"][3]) / 2 for line in lines)
        table_row = any(b - a < 1.5 for a, b in zip(heights, heights[1:]))
        spans = [s for line in lines for s in line["spans"] if s["text"].strip()]
        styled_heading = all(s["flags"] & (2 | 16) for s in spans)
        body = (not table_row and box[2] - box[0] > .18 * page.rect.width
                and len(re.findall(r"[A-Za-z]{2,}", text)) >= 5
                and not styled_heading and not LIST_OR_HEADING.match(text))
        result.append(Block(page.number, box, body=body, text=text, height=page.rect.height))
    return result


def _boundaries(blocks, width):
    """Return unambiguous reading-order edges plus an optional left/right column turn."""
    if not blocks:
        return None, None, None
    left = [b for b in blocks if b.bbox[2] <= .55 * width and b.bbox[0] < .35 * width]
    right = [b for b in blocks if b.bbox[0] >= .45 * width and b.bbox[2] > .65 * width]
    if not any(b.body for b in left) or not any(b.body for b in right):
        ordered = sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0]))
        return ordered[0], max(ordered, key=lambda b:b.bbox[3]), None

    left_tail = max(left, key=lambda b:b.bbox[3])
    right_head = min(right, key=lambda b:b.bbox[1])
    left_edge = max(b.bbox[2] for b in left)
    right_edge = min(b.bbox[0] for b in right)
    spans = [b for b in blocks if b not in left and b not in right]
    if (left_edge > right_edge + 2 or left_tail.bbox[3] <= right_head.bbox[1]
            or any(b.bbox[1] < left_tail.bbox[3] and b.bbox[3] > right_head.bbox[1] for b in spans)):
        return None, None, None
    start = min(left, key=lambda b:b.bbox[1])
    end = max(right, key=lambda b:b.bbox[3])
    head = min([replace(start, zone="left_column"), *spans], key=lambda b:b.bbox[1])
    tail = max([replace(end, zone="right_column"), *spans], key=lambda b:b.bbox[3])
    return head, tail, (replace(left_tail,zone="left_column"),replace(right_head,zone="right_column"))


class BoundaryIndex:
    def __init__(self):
        self.before = defaultdict(list)
        self.after = defaultdict(list)
        self.signatures = defaultdict(int)

    @classmethod
    def from_pdf(cls, path):
        import pymupdf
        index = cls()
        previous = None
        with pymupdf.open(path) as doc:
            for page in doc:
                blocks = _blocks(page)
                for b in blocks:
                    fingerprint = key(b.text)
                    index.signatures["head",fingerprint[:ANCHOR_LENGTH]] += 1
                    index.signatures["tail",fingerprint[-ANCHOR_LENGTH:]] += 1
                head, tail, turn = _boundaries(blocks, page.rect.width)
                if previous and head:
                    index._add(previous, head)
                if turn:
                    index._add(*turn)
                previous = tail
        return index

    def _add(self, left, right):
        from . import rules
        prev, after = left.payload("tail"), right.payload("head")
        if not left.body or not right.body or left.text.rstrip().endswith((".", "!", "?", ":", ";")):
            return
        if right.text[:1].isupper() or LIST_OR_HEADING.match(right.text):
            return
        if rules.starts_like_caption_title(left.text) or rules.starts_like_caption_title(right.text):
            return
        if left.bbox[3] < .60 * left.height or right.bbox[1] > .40 * right.height:
            return
        if left.page == right.page:
            if not rules.is_reading_boundary_cross_column_pair(prev, after):
                return
        elif (not rules.is_reading_boundary_cross_page_pair(prev, after) or
              (left.zone == right.zone == "single_column" and
               (abs(left.bbox[0] - right.bbox[0]) > 32 or
                min(left.bbox[2]-left.bbox[0], right.bbox[2]-right.bbox[0]) /
                max(left.bbox[2]-left.bbox[0], right.bbox[2]-right.bbox[0]) < .7))):
            return
        if rules.pair_decision(prev, after) != "join":
            return
        a, b = key(left.text), key(right.text)
        if min(len(a), len(b)) < ANCHOR_LENGTH:
            return
        self.after[a[-ANCHOR_LENGTH:]].append(right.text[:540])
        self.before[b[:ANCHOR_LENGTH]].append(left.text[-540:])

    def context_for(self, text):
        fingerprint = key(text)
        if len(fingerprint) < ANCHOR_LENGTH:
            return {}
        context = {}
        for direction, anchor, table, role in (
            ("preceding_text", fingerprint[:ANCHOR_LENGTH], self.before, "head"),
            ("following_text", fingerprint[-ANCHOR_LENGTH:], self.after, "tail"),
        ):
            matches = table.get(anchor, ())
            if len(matches) == 1 and self.signatures[role,anchor] == 1:
                context[direction] = matches[0]
        return context
