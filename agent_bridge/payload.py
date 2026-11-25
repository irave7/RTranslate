"""Compact a standalone batch without relying on a previous host turn."""
from collections import defaultdict
import json
import re


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _source(task):
    fields = ("text", "items", "context", "nearbyText", "nearbyLabels", "neighbors", "hints", "messages")
    return "\n".join(s for k in fields for s in _strings(task.get(k))).casefold().replace("- ", "").replace("\u00ad", "")


def _occurs(term, source):
    value = term.strip().casefold().replace("- ", "").replace("\u00ad", "")
    return bool(value and re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", source))


def _clean_hints(task):
    if "hints" not in task:
        return
    result = []
    for line in task["hints"].splitlines():
        title = re.fullmatch(r"\s*(?:\d+\.\s*)?(First title in (?:the )?full text|The most recent title is):\s*(.*)", line)
        if title:
            value = title[2].strip()
            # Some PDFs expose all-glyph-IDs as their first title. Only discard
            # it when the entire title consists of unmapped glyph codes.
            if re.fullmatch(r"(?:\(cid:\d+\)\s*)+", value):
                continue
            name = "Title" if title[1].startswith("First") else "Section"
            line = f"{name}: {value}"
        result.append(line)
    if result:
        task["hints"] = "\n".join(result)
    else:
        task.pop("hints")


def _share_context(batch):
    occurrences = defaultdict(list)
    for task in batch["tasks"]:
        for key in ("hints", "instructions", "nearbyText", "context"):
            if isinstance(task.get(key), str) and len(task[key]) >= 40:
                occurrences[task[key]].append((task, key))
        for row in task.get("neighbors", []):
            for key, value in row.items():
                if isinstance(value, str) and len(value) >= 40:
                    occurrences[value].append((row, key))
    shared = {}
    for value, places in occurrences.items():
        if len(places) < 2:
            continue
        ref = f"c{len(shared)+1}"
        if (len(value)-len(ref)) * (len(places)-1) <= 30:
            continue
        shared[ref] = value
        for obj, key in places:
            del obj[key]
            obj[key+"Ref"] = ref
    if shared:
        batch["sharedContext"] = shared


def streamline(batch):
    """Remove duplicate metadata and irrelevant glossary matches, preserving native slot order."""
    tasks = batch["tasks"]
    source_text = [_source(task) for task in tasks]
    supplied = {k:v for k,v in batch.get("glossary", {}).items() if any(_occurs(k, text) for text in source_text)}
    if supplied:
        batch["glossary"] = supplied
    else:
        batch.pop("glossary", None)
    priority = {k.casefold():v for k,v in supplied.items()}
    for task, source in zip(tasks, source_text):
        _clean_hints(task)
        layout = task.get("layout", [])
        if layout and all(value == layout[0] for value in layout):
            task["layout"] = layout[0]
        tables = []
        for table in task.get("glossaries", []):
            rows = [pair for pair in table["terms"] if _occurs(pair[0], source) and pair[0].casefold() not in priority]
            if rows:
                tables.append({**table, "terms": rows})
        if tables:
            task["glossaries"] = tables
        else:
            task.pop("glossaries", None)
        if task.get("kind") == "figure" and isinstance(task.get("glossary"), str):
            lines = []
            for line in task["glossary"].splitlines():
                name, eq, target = line.partition("=")
                if not eq or (_occurs(name, source) and name.strip().casefold() not in priority):
                    lines.append(line)
            if lines:
                task["glossary"] = "\n".join(lines)
            else:
                task.pop("glossary")
    _share_context(batch)
    return batch


def bounded_batch(tasks, code, select_terms, budget):
    """Include physical table hints and matching user terms in the final character budget."""
    from copy import deepcopy
    for count in range(len(tasks), 0, -1):
        batch = {"state":"working", "tasks":deepcopy(tasks[:count]), "batch":code}
        terms = select_terms(batch["tasks"])
        if terms:
            batch["glossary"] = terms
        streamline(batch)
        if count == 1 or len(json.dumps(batch, ensure_ascii=False, separators=(",", ":"))) <= budget:
            return batch


def packet(directory, destination, limit, wait):
    """Give the coordinator only a private filename and status; never echo paper content."""
    import os
    from .cli import next_batch
    from .jobs import read
    from .queue import TaskQueue
    path = destination.expanduser().absolute()
    # Reserve before claiming so a filename collision never takes a lease.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    batch = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            batch = next_batch(directory, limit, wait, True)
            if batch["tasks"]:
                json.dump(batch, out, ensure_ascii=False, separators=(",", ":"))
        if not batch["tasks"]:
            path.unlink()
            return {k:v for k,v in batch.items() if k != "tasks"}
        return {"state":batch["state"], "batch":batch["batch"], "file":str(path), "count":len(batch["tasks"])}
    except BaseException:
        path.unlink(missing_ok=True)
        if batch and batch.get("batch"):
            for ident, lease, *_ in read(directory/"tickets"/(batch["batch"]+".json")):
                TaskQueue(directory).release(ident, lease)
        raise
