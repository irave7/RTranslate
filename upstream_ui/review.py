"""Review exact paragraphs from BabelDOC's own translation tracker, then re-typeset them."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import pymupdf


BATCH_INPUT = "## Here is the input:"
SINGLE_INPUT = "Now translate the following text:"
MARKUP = re.compile(r"\{v\d+\}|</?style(?:\s+[^<>]+)?>|</?formula(?:\s+[^<>]+)?>", re.I)
TAGS = re.compile(r"<[^<>]+>")
RUNNING_CITATION = re.compile(
    r"[A-Za-z][A-Za-z .,&’'\-]{2,90}\s*(?:19|20)\d{2}\s*,\s*\d{1,5}\s*,"
    r"\s*\d{1,9}(?:[–-]\d+)?\.?"
)


def normalized(text):
    text = unicodedata.normalize("NFKC", TAGS.sub("", text))
    return re.sub(r"[\s\-\u00ad\u200b]", "", text).casefold()


def batch_items(prompt):
    if not isinstance(prompt,str) or BATCH_INPUT not in prompt:
        return []
    try:
        items = json.loads(prompt.rpartition(BATCH_INPUT)[2])
    except ValueError:
        return []
    if not isinstance(items,list) or not all(isinstance(i,dict) and
            isinstance(i.get("input"),str) and "id" in i for i in items):
        return []
    return items


def batch_key(items):
    # Prompts can include a glossary or other variable context. BabelDOC's item
    # list identifies this batch and each native slot identifies its paragraph.
    identity = [(item["id"],item["input"],item.get("layout_label")) for item in items]
    return hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()


def native_slot(entry, source):
    pos = entry.get("multi_paragraph_index")
    if not isinstance(pos,int) or pos<0:
        return None
    for tracker in entry.get("llm_translate_trackers",[]):
        items = batch_items(tracker.get("input"))
        if pos<len(items) and items[pos]["input"]==source:
            return [batch_key(items),pos]
    return None


def collect_segments(source, native, destination):
    files = list(native.rglob("translate_tracking.json"))
    records = []
    with pymupdf.open(source) as document:
        page_texts = [normalized(page.get_text()) for page in document]
    # The desktop job uses one native part. Each native page group has an explicit position.
    if len(files) == 1:
        raw = json.loads(files[0].read_text(encoding="utf-8"))
        for group_name in ("page", "cross_page", "cross_column"):
            for index, group in enumerate(raw.get(group_name, []), start=1):
                for entry in group.get("paragraph", []):
                    source_text, target = entry.get("input"), entry.get("output")
                    if not isinstance(source_text,str) or not isinstance(target,str) or not target.strip():
                        continue
                    page = index if group_name == "page" else None
                    if page is None:
                        anchor = normalized(entry.get("pdf_unicode") or source_text)[:48]
                        candidates = [i for i,t in enumerate(page_texts,1) if len(anchor)>=12 and anchor in t]
                        if len(candidates)==1:
                            page = candidates[0]
                    uid = f"{group_name}/{index}/{len(records)}/{source_text}"
                    row = dict(id=hashlib.sha256(uid.encode()).hexdigest()[:32],page=page,
                               source=source_text,translation=target)
                    slot = native_slot(entry,source_text)
                    if slot is not None:
                        row["slot"] = slot
                    records.append(row)
    counts = Counter(row["source"] for row in records)
    slot_counts = Counter(tuple(row["slot"]) for row in records if row.get("slot"))
    for row in records:
        stable_slot = bool(row.get("slot") and slot_counts[tuple(row["slot"])] == 1)
        original = Counter(MARKUP.findall(row["source"]))
        translated = Counter(MARKUP.findall(row["translation"]))
        row["restoreMarkup"] = bool(stable_slot and original != translated)
        row["editable"] = ((stable_slot or counts[row["source"]] == 1) and row["page"] is not None
                           and (row["restoreMarkup"] or not (original - translated)))
    destination.write_text(json.dumps(records,ensure_ascii=False), encoding="utf-8")


def apply_edits(rows, edits):
    if not isinstance(edits,list) or not 0 < len(edits) <= 1500:
        raise ValueError("请提交至少一个已修改的段落，每次最多 1500 段。")
    by_id = {row["id"]:row for row in rows if row["editable"]}
    seen=set()
    for edit in edits:
        if not isinstance(edit,dict):
            raise ValueError("校订内容无效。")
        ident=edit.get("id")
        row=by_id.get(ident)
        target=edit.get("translation")
        if not row or ident in seen or not isinstance(target,str) or not 0 < len(target.strip()) <= 30000:
            raise ValueError("段落不属于当前版本，或译文为空、过长。")
        # A uniquely located paragraph can recover symbols lost by the model.
        # Accept exactly the source set; an invented formula ID is never valid.
        reference = row["source"] if row.get("restoreMarkup") else row["translation"]
        if Counter(MARKUP.findall(reference)) != Counter(MARKUP.findall(target)):
            raise ValueError("请按原文恢复公式、引文和样式标记。" if row.get("restoreMarkup")
                             else "请保留当前译文中的公式与样式标记。")
        if Counter(TAGS.findall(target)) - Counter(TAGS.findall(reference)):
            raise ValueError("译文中出现了新的样式标记。")
        seen.add(ident)
        if row["translation"] != target.strip():
            row["reviewed"] = True
        row["translation"]=target.strip()
    return rows


def restore_running_citations(rows, source):
    """Recover lost styles in unedited running citations before native batch replay."""
    def line_key(text):
        return "".join(unicodedata.normalize("NFKC", text).split())

    citations = defaultdict(list)
    for row in rows:
        text = row["source"]
        if not isinstance(row.get("page"), int) or "<style" not in text:
            continue
        plain = TAGS.sub("", text).strip()
        if RUNNING_CITATION.fullmatch(plain) and all(
                tag == "</style>" or tag.startswith("<style ") for tag in TAGS.findall(text)):
            citations[line_key(plain)].append(row)

    candidates = defaultdict(dict)
    for plain, group in citations.items():
        page_counts = Counter(row["page"] for row in group)
        if len(page_counts) < 3:
            continue
        # Require a single native occurrence on the page. Even a matching
        # line in its margin cannot locate two identical native paragraphs.
        for row in group:
            if row.get("slot") and page_counts[row["page"]] == 1:
                candidates[plain][row["page"]] = row

    def damaged_style(row):
        if row.get("reviewed") or not row.get("editable"):
            return False
        original, translated = row["source"], row["translation"]
        if row.get("restoreMarkup") and Counter(MARKUP.findall(original)) - Counter(MARKUP.findall(translated)):
            return True
        # The journal title, date and volume are all still present, but style
        # tags can have migrated from the date to the title in model output.
        return (line_key(TAGS.sub("", original)) == line_key(TAGS.sub("", translated))
                and line_key(original) != line_key(translated))

    pending = {text: group for text, group in candidates.items() if any(map(damaged_style, group.values()))}
    if not pending:
        return
    verified = defaultdict(list)
    with pymupdf.open(source) as document:
        for number in sorted({p for group in pending.values() for p in group}):
            if not 1 <= number <= len(document):
                continue
            page = document[number - 1]
            height = page.cropbox.height
            lines = defaultdict(list)
            for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]:
                for line in block.get("lines", []):
                    key = line_key("".join(span["text"] for span in line["spans"]))
                    if key in pending and number in pending[key]:
                        lines[key].append(line["bbox"])
            for key, occurrences in lines.items():
                if len(occurrences) == 1:
                    _, y0, _, y1 = occurrences[0]
                    edge = "top" if y1 <= height * .10 else "bottom" if y0 >= height * .90 else None
                    if edge:
                        verified[key, edge].append(pending[key][number])
    for group in verified.values():
        if len(group) >= 3:
            for row in group:
                if damaged_style(row):
                    row["translation"] = row["source"]
                    row["restoreMarkup"] = False


class ReviewReplay:
    """Provide already accepted paragraph text to the native renderer without model calls."""
    def __init__(self, engine, path):
        self.engine = engine
        self.texts = {}
        self.slots = {}
        self.terminal_gateway_error = ""
        for row in json.loads(path.read_text(encoding="utf-8")):
            # A native single-item retry strips exterior whitespace before
            # typesetting. Preserve exact strings in the positional batch map.
            self._record(self.texts, row["source"].strip(), row["translation"].strip())
            if row.get("slot"):
                self._record(self.slots, tuple(row["slot"]), (row["source"], row["translation"]))

    @staticmethod
    def _record(index,key,target):
        if index.setdefault(key,target)!=target:
            index[key] = None

    def __getattr__(self, name):
        return getattr(self.engine,name)

    def _lookup(self, source, slot=None):
        if slot in self.slots:
            # Bind both the original text and its position. Conflicting or
            # stale slots cannot be repaired by borrowing another paragraph.
            entry = self.slots[slot]
            result = entry[1] if entry and entry[0] == source else None
        else:
            # A single-item retry carries no location and strips surrounding
            # whitespace. It is safe only when all possible rows agree.
            result = self.texts.get(source.strip())
        if not result:
            self.terminal_gateway_error = "原生分段与当前译本不一致，无法安全重排。原译本仍可下载。"
            raise RuntimeError(self.terminal_gateway_error)
        return result

    def llm_translate(self, text, ignore_cache=False, rate_limit_params=None):
        if text is None:
            return None
        rows = batch_items(text)
        if rows:
            key = batch_key(rows)
            return json.dumps([{"id":row["id"],"output":self._lookup(row["input"],(key,i))}
                               for i,row in enumerate(rows)],ensure_ascii=False)
        if SINGLE_INPUT in text:
            return self._lookup(text.rpartition(SINGLE_INPUT)[2].strip())
        return self._lookup(text)

    def do_llm_translate(self, text, rate_limit_params=None):
        return self.llm_translate(text, rate_limit_params=rate_limit_params)

    def translate(self, text, ignore_cache=False, rate_limit_params=None):
        return self._lookup(text)

    def do_translate(self, text, rate_limit_params=None):
        return self._lookup(text)


def suggest(entry,rows,config,endpoint,model,key):
    from pdf2zh_next.translator.translator_impl.freellmapi import unchanged_literal
    if unchanged_literal(entry["source"]):
        return entry["source"]
    from job import ACADEMIC_STYLE
    from pdf2zh_next.config.model import SettingsModel
    from pdf2zh_next.config.translate_engine_model import FreeLLMAPISettings
    from pdf2zh_next.translator.utils import get_translator
    from pdf2zh_next.translator.named_numbers import preserve_named_number
    provider=FreeLLMAPISettings(freellmapi_base_url=endpoint,freellmapi_api_key=key,freellmapi_model=model)
    provider._direct_api=config["mode"] == "api"
    provider._direct_local=config["mode"] in ("builtin","local")
    provider._builtin_model=config["mode"] == "builtin"
    provider._user_terms=config["glossary"]
    settings=SettingsModel(translate_engine_settings=provider)
    settings.translation.lang_in,settings.translation.lang_out="en","zh-CN"
    engine=get_translator(settings)
    i=rows.index(entry)
    adjacent=[r["source"][:900] for r in rows[max(0,i-1):i+2] if r is not entry and r["page"]==entry["page"]]
    prompt=(ACADEMIC_STYLE+"\n重译下面的选中段落。仅输出该段的中文译文，保持公式和样式标记。\n"
            +json.dumps({"术语表":config["glossary"],"相邻原文供理解":adjacent,"选中段落":entry["source"]},ensure_ascii=False))
    try:
        return preserve_named_number(entry["source"], engine.llm_translate(prompt,ignore_cache=True).strip())
    finally:
        engine.client.close()
