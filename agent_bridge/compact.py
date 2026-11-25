"""Omit unambiguous, immutable numeric cells from mixed native paragraph batches."""
import re
from .short_labels import protected

NUMBERS = re.compile(r"[\d\s.,:;()\[\]%‰+−–—/×÷<>=±*⁎†‡-]+")


def omit_literals(task):
    if task["kind"] != "paragraphs":
        return None
    items = task["items"]
    sent = [i for i,text in enumerate(items) if not NUMBERS.fullmatch(text) and not protected(text)]
    if len(sent) == len(items):
        return None
    task["items"] = [items[i] for i in sent]
    if "layout" in task:
        task["layout"] = [task["layout"][i] for i in sent]
    if "neighbors" in task:
        positions = {old:new for new,old in enumerate(sent)}
        task["neighbors"] = [{**neighbor, "index":positions[neighbor["index"]]}
                             for neighbor in task["neighbors"] if neighbor["index"] in positions]
    return sent


def restore(items, sent, answer):
    if not isinstance(answer,list) or len(sent)!=len(answer):
        raise ValueError(f"请按本批给出的 {len(sent)} 条原文顺序填写译文。")
    out = list(items)
    for i,text in zip(sent,answer):
        out[i] = text
    return out
