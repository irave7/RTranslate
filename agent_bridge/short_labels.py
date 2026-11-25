"""Preserve compact scientific notation; give short English words local document context."""
import re
from collections import defaultdict


# A capitalized table heading can be an ordinary word. Let the host determine
# its meaning (e.g. Open surgery vs. an open trial); never assign a fixed gloss.
_WORDS = set("YES NO AGE SEX MALE OPEN DIET DAY DAYS DOSE MEAN NORM HIGH LOW NONE ALL BOTH ARM TIME RATE RISK COST MASS AREA SITE TYPE TEST CASE DATA PAIN FATS FAT FOOD LIVE DEAD LEFT BEST POOR GOOD COLD HOT FAST SLOW MILD LATE ON OFF OUT END PRE POST LOG MIN MAX SUM".split())
_UNITS = set("mg kg g ng μg µg mL ml dL cm mm km nm ns ms Hz mV mM kPa cmHg mmHg kDa kHz GHz mmol mol °C °F hPa pH Na Mg Fe Cl Zn Ca".split())


def protected(source):
    token = source.strip()
    if not 1 <= len(token) <= 4:
        return False
    return (token in _UNITS or not any(c.isalpha() for c in token)
            or bool(re.fullmatch(r"[A-Z0-9][A-Z0-9/+.−-]*",token) and token not in _WORDS)
            or bool(re.fullmatch(r"[A-Za-z]{1,2}\d{1,2}",token)))


def contexts(source):
    """Find nearby row/column labels in the original PDF text layer, before translation."""
    import pymupdf
    catalog = defaultdict(list)
    with pymupdf.open(source) as doc:
        for page in doc:
            lines = []
            for block in page.get_text("dict",flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]:
                for line in block.get("lines",[]):
                    text = "".join(s["text"] for s in line["spans"]).strip()
                    if 1 < len(text) <= 60 and re.search(r"[A-Za-z]",text):
                        x,y,right,bottom = line["bbox"]
                        lines.append((text,x,(y+bottom)/2,bottom-y))
            for text,x,y,height in lines:
                if not re.fullmatch(r"[A-Za-z]{2,4}",text) or protected(text) or len(catalog[text]) >= 12:
                    continue
                nearest = []
                for other, sx, sy, _ in lines:
                    dy, dx = abs(sy-y), abs(sx-x)
                    same_row = dy <= height*.55 and dx <= page.rect.width*.65
                    if other != text and (same_row or (dy <= height*4 and dx <= 145)):
                        nearest.append((not same_row, dy + dx/3, other))
                labels = list(dict.fromkeys(other for _,_,other in sorted(nearest)))[:6]
                if labels and labels not in catalog[text]:
                    catalog[text].append(labels)
    return dict(catalog)


def annotate(task, catalog):
    """Use nearby text only when its physical occurrence is unique or evidence agrees."""
    items = task.get("items") if task["kind"] == "paragraphs" else [task["text"]] if task["kind"] == "single" else []
    anchors = {s.casefold() for s in items or []}
    context = {}
    for index, label in enumerate(items or []):
        candidates = catalog.get(label.strip())
        if not candidates:
            continue
        scored = [(sum(s.casefold() in anchors for s in c),c) for c in candidates]
        best = max(score for score,_ in scored)
        matches = [c for score,c in scored if score == best]
        # Never silently choose between different meanings of repeated labels.
        shared = [s for s in matches[0] if all(s in c for c in matches[1:])]
        if shared:
            context[index] = shared
    if context:
        task["nearbyLabels"] = context
