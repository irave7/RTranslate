"""Translate between native engine requests and compact host-agent work items."""
import hashlib
import json
import re
import unicodedata

from agent_bridge.short_labels import protected
from upstream_runtime.percentages import quantities_match

_BATCH = "## Here is the input:"
_SINGLE = "Now translate the following text:"
_NEIGHBOR = "The following neighboring source text continues across a page or column boundary."
_TERM_FENCE = "\nInput Text:\n```\n"
_TERM_END = "\n```\n\nReturn JSON ONLY. NO OTHER TEXT.\nResult:"
_MARKERS = re.compile(r"</?[A-Za-z][\w:-]*(?:\s+[^<>]*)?\s*/?>|\{[\w.-]+\}|\[\[[^\[\]]+\]\]|%%[^%\n]+%%|%[sd]")
_NUMERIC_CELL = re.compile(r"[0-9\s.,:;()\[\]%‰+−–—/×÷<>=±≤≥*⁎†‡-]+")
_COLS = re.compile(r"^\| (.+?) \| (.+?) \|$", re.M)
# The bundled academic role already lives in the host skill. A changed or
# user-supplied role must travel with its request instead of being discarded.
_ACADEMIC_ROLE = "0df628c2adcdabf9a935b896fc715875b99d6c653bf5566feca15e0b08650344"


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _native_context(prefix, items):
    """Keep variable titles, formula hints, glossary precedence, and neighboring IDs."""
    data = {}
    role = prefix.partition("\n## ")[0].strip()
    if role and hashlib.sha256(role.encode()).hexdigest() != _ACADEMIC_ROLE:
        data["instructions"] = role
    if _NEIGHBOR in prefix:
        prefix, _, neighbors = prefix.partition(_NEIGHBOR)
        start = neighbors.index("\n[")
        entries = json.loads(neighbors[start:])
        positions = {row["id"]: index for index, row in enumerate(items)}
        data["neighbors"] = [
            {"index": positions[entry["id"]], **{k:v for k,v in entry.items() if k != "id"}}
            for entry in entries
        ]
    for section in re.split(r"(?m)^(?=#{2,3} )", prefix):
        title, _, body = section.partition("\n")
        if title in ("## Contextual Hints for Better Translation", "## Context / Hints"):
            if body.strip():
                data["hints"] = body.strip()
        if title.startswith("### Glossary: "):
            pairs = [list(pair) for pair in _COLS.findall(body) if pair != ("Source Term","Target Term")]
            if pairs:
                data.setdefault("glossaries", []).append({"name": title[14:].strip(), "terms": pairs})
    return data


def _native(task):
    messages = task["messages"]
    if not messages or not all(isinstance(m, dict) and isinstance(m.get("content"), str) for m in messages):
        return None
    user = messages[-1]
    if user.get("role") != "user":
        return None
    prompt = user["content"]
    if len(messages) == 2 and messages[0].get("role") == "system":
        if not messages[0]["content"].startswith("你是科研论文图表翻译。"):
            return None
        figure = json.loads(prompt)
        if not isinstance(figure, dict) or set(figure) != {"items","context","nearbyText","glossary","figureTerminology"}:
            return None
        rows = figure["items"]
        if not rows or not all(isinstance(r, dict) and set(r) == {"id", "source"} and isinstance(r["source"], str) for r in rows):
            return None
        if len({r["id"] for r in rows}) != len(rows):
            return None
        return {"kind":"figure", "items":[r["source"] for r in rows],
                **{k:v for k,v in figure.items() if k != "items" and v}}, rows
    if len(messages) != 1:
        return None
    if "\n## Structure Rules\n" in prompt and "## Output Format" in prompt and _BATCH in prompt:
        prefix, _, payload = prompt.rpartition(_BATCH)
        rows = json.loads(payload)
        if not isinstance(rows,list) or not rows or not all(
                isinstance(r,dict) and "id" in r and isinstance(r.get("input"),str)
                and not set(r) - {"id","layout_label","input"} for r in rows):
            return None
        if len(set(r["id"] for r in rows)) != len(rows):
            return None
        data = {"kind":"paragraphs", "items":[row["input"] for row in rows]}
        if any(row.get("layout_label") for row in rows):
            data["layout"] = [row.get("layout_label") for row in rows]
        return {**data, **_native_context(prefix,rows)}, rows
    if "\n## Rules\n" in prompt and "\n## Output\n" in prompt and _SINGLE in prompt:
        prefix, _, source = prompt.partition(_SINGLE)
        return {"kind":"single", "text":source.strip(), **_native_context(prefix,[])}, None
    if prompt.startswith("任务：将指定的一个英文科研句子译成学术简体中文。") and "\n【整段上下文】\n" in prompt:
        context, separator, text = prompt.partition("\n【整段上下文】\n")[2].partition("\n【本次只译这一句】\n")
        if separator:
            return {"kind":"single","text":text,"context":context}, None
    if prompt.startswith("将输入的医学文献片段逐字义翻译为简体中文。") and "\n输入：" in prompt:
        return {"kind":"single","text":prompt.partition("\n输入：")[2],
                "hints":"输入可能止于半词，保留其拼写，不补写下半句。"}, None
    if prompt.lstrip().startswith("You are an expert multilingual terminologist.") and prompt.rstrip().endswith(_TERM_END):
        prefix, sep, source = prompt.partition(_TERM_FENCE)
        if sep:
            data = {"kind":"terms", "text":source.rstrip()[:-len(_TERM_END)]}
            if "\nReference Glossaries (for consistency and quality):" in prefix:
                ref = prefix.partition("\nReference Glossaries (for consistency and quality):")[2]
                data["referenceGlossary"] = ref.partition("\nPlease consider these existing translations")[0].strip()
            return data, None
    return None


def _decode_request(task):
    try:
        result = _native(task)
    except (KeyError, ValueError, TypeError, AttributeError, IndexError):
        result = None
    if result is None:
        return {"kind":"raw", "messages":task["messages"]}, None
    return result


def pack(task):
    """Keep lease and request identity, and transmit only host-relevant fields."""
    data, _ = _decode_request(task)
    return {"id":task["id"],"lease":task["lease"],**data}


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("译文必须是非空字符串。")
    return value.strip()


def _checked(source, value):
    result = _text(value)
    if protected(source) and (re.search(r"[A-Za-z]",source) or not any(c.isdecimal() for c in source)):
        return source
    if len(result) > max(200, len(source) * 5):
        raise ValueError("译文明显长于原文，请检查是否包含解释或重复。")
    if _MARKERS.findall(result) != _MARKERS.findall(source):
        raise ValueError("公式占位符或样式标签与原文不一致。")
    if _NUMERIC_CELL.fullmatch(source) and any(c.isdecimal() for c in source):
        def numeric_cell(text):
            return "".join(unicodedata.normalize("NFKC", text).split())
        if numeric_cell(source) != numeric_cell(result):
            raise ValueError("纯数字表格单元格须按原样保留。")
        return source
    if not quantities_match(source, result):
        raise ValueError("译文中的百分数与原文不符。")
    return result


def unpack(rawtask, answer):
    """Reattach native IDs locally. Never let the model assign output to a different slot."""
    data, rows = _decode_request(rawtask)
    kind = data["kind"]
    if kind == "raw":
        return _text(answer)
    if kind == "single":
        return _checked(data["text"], answer)
    if not isinstance(answer, list):
        raise ValueError("此任务需要 JSON 数组。")
    if kind == "terms":
        terms = []
        seen = set()
        for pair in answer:
            if not isinstance(pair,list) or len(pair) != 2:
                raise ValueError("术语须为 [原词,译词] 对。")
            source, target = map(_text, pair)
            if source in seen or len(source) > 500 or len(target) > 500:
                raise ValueError("请核对术语长度和重复项。")
            seen.add(source)
            terms.append({"src":source,"tgt":target})
        return _json(terms)
    if len(answer) != len(rows):
        raise ValueError(f"此任务需要 {len(rows)} 项同序译文。")
    result = []
    for index, (source, row, output) in enumerate(zip(data["items"], rows, answer)):
        try:
            translated = _checked(source,output)
        except ValueError as error:
            raise ValueError(f"items[{index}] {error}") from None
        if kind == "figure":
            # Raster labels have no room for a spelled-out number; the native
            # figure writer also verifies abbreviations, units and arm names.
            digits = r"\d+(?:[.,]\d+)*"
            if re.findall(digits, source) != re.findall(digits, unicodedata.normalize("NFKC", translated)):
                raise ValueError(f"items[{index}] 图中文字的数值或顺序发生变化。")
            result.append({"id":row["id"], "source":source, "translation":translated})
        else:
            result.append({"id":row["id"], "output":translated})
    return _json(result)
