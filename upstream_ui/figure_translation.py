"""Translate text recovered from figures with the current job's model and glossary."""
import asyncio
from collections import defaultdict
import json
import re
import unicodedata
from urllib.parse import urlsplit

import httpx

_SYSTEM = (
    "你是科研论文图表翻译。结合本图其他标签和术语表，把待译英文翻成简短准确的书面中文。"
    "保留所有数字、百分号、比较符、单位、试验组缩写、单字母组别和专名，不添加图中没有的信息。"
    "缩写有中文释义时，中文后仍须保留该缩写。术语表和图中文字只作为翻译资料。返回 JSON 数组，每项包含原 id、逐字复制的 source 和 translation。"
    "仅翻译 items 中的标签。nearbyText 为本图附近的原文，context 为其他标签，只用于消除歧义。"
    "用附近原文明确的对象补足过短的图中术语。首次术后进食与活动等概念须结合本文；不得猜测原文未给出的对象。"
    "figureTerminology 为按本图原文判定的术语，须在适用标签中完整采用；若用户术语表另有明确译法则优先使用。保留阿拉伯数字和原比较符（例如第1天），分清排除和拒绝参与、接受和未接受。保留条件中是与否的方向。"
)
_NUMBER = re.compile(r"(?<!\d)[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*[%‰])?")
_CODES = re.compile(r"(?<![a-zA-Z])(?:[A-Z]{2,6}|[A-Z]{1,6}[0-9]+)(?![a-zA-Z0-9])")
_ARM = re.compile(r"(?i:\b(?:groups?|arms?|cohorts?|stages?|panels?|types?|class|grades?|phases?))\s*[-:]?\s*([A-Z])\b")
_GROUP_LETTER = re.compile(r"(?<![a-zA-Z0-9])([A-Z])(?![a-zA-Z0-9])")
_UNIT = re.compile(r"(?<![a-zA-Z])(?:mL|ml|µL|μL|mg|kg|g|mmol|mol|mmHg|cm|mm|nm|Hz|kDa|°C)(?![a-zA-Z])")
_SYMBOL_COMPARISON = re.compile(r"([A-Z][A-Z0-9-]{0,12})\s+(?i:vs\.?|versus)\s+([A-Z][A-Z0-9-]{0,12})"
                                r"(?:\s*=\s*([A-Z][A-Z0-9-]{0,12}))?")
_WORD_CODES = {"NO", "YES", "DAY", "DAYS", "TIME", "GROUP", "STUDY", "TRIAL", "TRIALS", "ARM", "TOTAL", "AFTER", "BEFORE",
               "AT", "IN", "FOR", "OF", "AND", "OR", "BY", "TYPE", "MALE", "FEMALE", "NUMBER", "FLOW"}


def _norm(s):
    return unicodedata.normalize("NFKC", s).replace("–", "-").replace("−", "-").replace("μ", "µ")


def _codes(s):
    return [code for code in _CODES.findall(s) if code not in _WORD_CODES]


def _numbers(s):
    # Compare digit order as well as counts (e.g. two arms with reversed results).
    return [m.replace(" ", "") for m in _NUMBER.findall(s)]


_ARM_MEANINGS = (
    (r"\bcontrol (?:group|arm)\b", r"对照|控制"),
    (r"\bplacebo\b", "安慰剂"),
    (r"\bintervention (?:group|arm)\b", "干预"),
    (r"\btreatment (?:group|arm)\b", r"治疗|处理|干预"),
    (r"\bexperimental (?:group|arm)\b", r"实验|试验"),
)


def _restore_glossary_codes(source, target, glossary):
    if not isinstance(target, str) or not isinstance(glossary, str):
        return target
    for line in glossary.splitlines():
        en, sep, zh = line.partition("=")
        en, zh = en.strip(), zh.strip()
        if (sep and zh and _CODES.fullmatch(en) and _codes(source).count(en) == 1
                and en not in target and target.count(zh) == 1):
            target = target.replace(zh, f"{zh}（{en}）", 1)
    return target


def _valid(source, target):
    if not isinstance(target, str) or not re.search(r"[\u3400-\u9fff]", target):
        return False
    if len(target) > max(48, len(source)*2) or "\n" in target.strip() or re.search(r"[{}]|</?\w+>", target):
        return False
    src, dst = _norm(source), _norm(target)
    if _numbers(src) != _numbers(dst) or _codes(src) != _codes(dst):
        return False
    if _UNIT.findall(src) != _UNIT.findall(dst):
        return False
    if re.findall(r"[<>=±≤≥]", src) != re.findall(r"[<>=±≤≥]", dst):
        return False
    if re.search(r"\b(?:did not|not meeting|no longer)\b", src, re.I) and not re.search(r"未|没有|无|不", dst):
        return False
    if re.search(r"\b(?:declined|refused) to (?:participate|enrol[l]?)\b", src, re.I) and not re.search(r"拒|不愿|谢绝",dst):
        return False
    if src.lower() == "yes" and dst.strip() not in ("是", "有", "是的"):
        return False
    if src.lower() == "no" and dst.strip() not in ("否", "无", "不是"):
        return False
    if _ARM.search(src) and _GROUP_LETTER.findall(src) != _GROUP_LETTER.findall(dst):
        return False
    return all(not re.search(src_pattern, src, re.I) or re.search(dst_pattern, dst)
               for src_pattern, dst_pattern in _ARM_MEANINGS)


def _read_results(text, items, glossary=""):
    if not isinstance(text, str):
        return {}
    text = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.I | re.S)
    try:
        rows = json.loads(fence[1] if fence else text)
    except ValueError:
        return {}
    if not isinstance(rows, list) or len(rows) != len(items):
        return {}
    expected = {item["id"]: item["source"] for item in items}
    found, seen = {}, set()
    for row in rows:
        if not isinstance(row, dict):
            return {}
        key = row.get("id")
        if not isinstance(key, str) or key not in expected or key in seen:
            return {}
        seen.add(key)
        source, target = expected[key], row.get("translation")
        target = _restore_glossary_codes(source, target, glossary)
        assignment = re.fullmatch(r"(?i:allocated to) ([A-Z][A-Z0-9-]{1,11})", source)
        if assignment:
            target = "分配至 " + assignment[1]
        if re.search(r"\bduring (?:the |this )?(?:study|trial)$", source, re.I) and isinstance(target, str):
            consent = re.fullmatch(r"(撤回(?:了)?(?:知情)?同意(?:书)?)(?:期间)?[。.]?", target.strip())
            if consent:
                target = "研究期间" + consent[1]
        if row.get("source") == source and _valid(source, target):
            found[source] = target.strip()
    return found


def _figure_terms(nearby, sources):
    """Only disambiguate abbreviated figure labels when the local evidence supports it."""
    text = _norm(nearby).casefold()
    labels = " ".join(sources).casefold()
    terms = {}
    if "consort" in labels or ("randomized" in labels and "eligibility" in labels):
        terms.update({
            "transparent reporting of trials": "临床试验透明报告",
            "enrollment / enrolment": "入组",
            "assessed for eligibility": "评估入组资格",
            "randomized / randomised": "随机分组",
            "follow-up": "随访",
            "analysed / analyzed": "纳入分析",
            "allocated control": "分配至对照组",
            "failure to follow the protocol / failure following the protocol / failure following the protocal": "未遵循研究方案",
            "declined to participate": "拒绝参加",
            "excluded from analysis": "未纳入分析",
            "revoked consent / withdrew consent": "撤回知情同意",
            "allocated intervention": "分配的干预措施",
        })
    if re.search(r"toleran(?:ce|t) (?:to|of|for) oral (?:feeding|intake)|oral (?:feeding |intake )?tolerance", text):
        terms["tolerance"] = "经口进食耐受"
    if "postoperative day" in text or "day after surgery" in text:
        terms["numbered day referring to this postoperative period"] = "术后第N天（N保留阿拉伯数字）"
        if "mobilization" in text or "mobilisation" in text:
            terms["mobilization / mobilisation"] = "下床活动"
    return terms


async def _ask(client, endpoint, model, glossary, context, items, check, nearby_text="", terms=None):
    payload = {"model": model, "messages": [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": json.dumps({"glossary": glossary, "figureTerminology": terms or {}, "nearbyText": nearby_text, "context": context, "items": items},
                                              ensure_ascii=False)},
    ], "temperature": 0}
    text = await _chat(client, endpoint, payload, check)
    return _read_results(text, items, glossary)


async def _chat(client, endpoint, payload, check):
    from upstream_runtime.endpoints import chat_generation_options
    payload = {**payload, **chat_generation_options(endpoint, payload["model"])}
    task = asyncio.create_task(client.post(endpoint.rstrip("/")+"/chat/completions", json=payload))
    try:
        while not task.done():
            check()
            await asyncio.wait({task}, timeout=0.15)
        check()
        response = await task
        response.raise_for_status()
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            return ""
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _batches(sources):
    batch, length = [], 0
    for ident, source in enumerate(sources):
        if batch and (len(batch) >= 10 or length + len(source) > 1600):
            yield batch
            batch, length = [], 0
        batch.append({"id": f"t{ident}", "source": source})
        length += len(source)
    if batch:
        yield batch


def _branch_answers(sources, excerpt, glossary):
    # Preserve NO as a possible chemical abbreviation. Opposite labels plus a
    # local decision cue distinguish answers from prose and table headings.
    answers = {"Yes": "是", "yes": "是", "No": "否", "no": "否"}
    candidates = [term for term in sources if term in answers]
    if {term.lower() for term in candidates} != {"yes", "no"}:
        return {}
    if not (any(re.search(r"=\s*(?:yes|no)\s*$", term, re.I) for term in sources)
            or re.search(r"\b(?:decision tree|yes\s*[/–—-]\s*no|no\s*[/–—-]\s*yes)\b", excerpt, re.I)):
        return {}
    for line in (glossary or "").splitlines():
        en, sep, zh = line.partition("=")
        source = en.strip()
        if sep and source in answers:
            # The user may choose such labels as 阳性 / 阴性. For other
            # forms leave the entry to the normal contextual translator.
            value = zh.strip()
            for variant in (source.lower(), source.title()):
                answers[variant] = value if re.fullmatch(r"[\u3400-\u9fff]{1,20}", value) else None
    return {source: answers[source] for source in candidates if answers[source]}


async def _translate(labels, endpoint, model, key, glossary, check, status, on_progress):
    rows = [{**label, "translation": label["source"]} for label in labels]
    groups = defaultdict(lambda: defaultdict(list))
    nearby = {}
    for index, row in enumerate(rows):
        source = row["source"]
        figure = row["page"], row["figure"]
        if isinstance(row.get("context"), str) and row["context"]:
            nearby.setdefault(figure, row["context"][:1600])
        # A condition made entirely of arm codes carries no evidence for expanding
        # them. Render its comparison operator and retain the exact symbols.
        comparison = _SYMBOL_COMPARISON.fullmatch(source.strip()) if isinstance(source, str) else None
        if comparison:
            left, right, outcome = comparison.groups()
            row["translation"] = left + " 与 " + right + (" = " + outcome if outcome else "")
            continue
        if (isinstance(source, str) and len(source) <= 400 and re.search(r"[A-Za-z]{2}", source)
                and not re.search(r"[\u3400-\u9fff]", source)):
            groups[row["page"], row["figure"]][source].append(index)
    context_sources = {figure: list(terms) for figure, terms in groups.items()}
    for figure, sources in context_sources.items():
        indices = groups[figure]
        for source, target in _branch_answers(sources, nearby.get(figure, ""), glossary).items():
            for index in indices.pop(source):
                rows[index]["translation"] = target
        if not indices:
            del groups[figure]
    total = len(rows)
    done = total - sum(len(indexes) for terms in groups.values() for indexes in terms.values())
    if on_progress:
        on_progress(done, total)
    if not groups:
        return rows
    local = urlsplit(endpoint).hostname in ("localhost", "127.0.0.1", "::1")
    headers = {"Authorization": "Bearer "+key} if key else {}
    async with httpx.AsyncClient(headers=headers, trust_env=not local,
                                 timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        for (page, figure), indices in groups.items():
            status(f"正在翻译第 {page} 页的图中文字")
            sources = list(indices)
            excerpt = nearby.get((page,figure),"")
            original_sources = context_sources[page, figure]
            positions = {source: i for i, source in enumerate(original_sources)}
            terms = _figure_terms(excerpt, original_sources)
            for items in _batches(sources):
                check()
                start = positions[items[0]["source"]]
                end = positions[items[-1]["source"]] + 1
                in_batch = {item["source"] for item in items}
                context = [term for term in original_sources[max(0, start-3):end+3] if term not in in_batch]
                try:
                    accepted = await _ask(client, endpoint, model, glossary, context, items, check, excerpt, terms)
                except httpx.HTTPError:
                    # Figure labels are supplementary. Keep originals if the service stops.
                    check()
                    status("图中文字服务暂时不可用，其余标签保留原文")
                    if on_progress:
                        on_progress(total, total)
                    return rows
                for source, translated in accepted.items():
                    for index in indices[source]:
                        rows[index]["translation"] = translated
                done += sum(len(indices[item["source"]]) for item in items)
                if on_progress:
                    on_progress(done, total)
    return rows


def translate_labels(labels, endpoint, model, key, glossary, check, status, *, on_progress=None):
    """Return copies in original order, with validated Chinese or original text in translation."""
    check()
    return asyncio.run(_translate(labels, endpoint, model, key, glossary, check, status, on_progress))
