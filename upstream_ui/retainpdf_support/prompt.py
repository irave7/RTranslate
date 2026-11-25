"""Attach neighboring text outside BabelDOC's input JSON, preserving IDs and boundaries."""
import json
import logging

from .document import BoundaryIndex

MARKER = "## Here is the input:"
logger = logging.getLogger(__name__)


def prepare_document(translator, source):
    # Only FLA's document translator consumes this optional attribute.
    if not getattr(translator, "accepts_boundary_context", False):
        return
    try:
        translator.boundary_index = BoundaryIndex.from_pdf(source)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("Unable to read PDF boundary context (%s)", type(exc).__name__)


def add_boundary_context(prompt, index):
    if not index or not prompt or MARKER not in prompt:
        return prompt
    prefix, marker, payload = prompt.rpartition(MARKER)
    try:
        items = json.loads(payload)
    except (ValueError, TypeError):
        return prompt
    if not isinstance(items, list):
        return prompt
    contexts = []
    for item in items:
        if not isinstance(item, dict) or item.get("layout_label") != "plain text":
            continue
        source = item.get("input")
        if not isinstance(source, str) or "id" not in item:
            continue
        match = index.context_for(source)
        if match:
            contexts.append(dict(id=item["id"], **match))
    if not contexts:
        return prompt
    extra = ("\nThe following neighboring source text continues across a page or column boundary. "
             "Use it only to understand its specified input ID. Translate only that ID's input fragment. "
             "First translate the complete sentence mentally, then split it into Chinese clauses matching the "
             "original ID boundary. 对跨页句先按整句译成自然的中文，再把分句分配到原有 ID。"
             "中文数量短语通常把范围或组别放在数量前。 "
             "Choose natural Chinese word order so that the two translated fragments read as one continuous "
             "sentence when placed next to each other. Avoid repeating the adjoining fragment's subject or clause. "
             "Never copy its neighbor into the output, add IDs, or change the location of numbers or placeholders.\n"
             + json.dumps(contexts, ensure_ascii=False) + "\n\n")
    return prefix + extra + marker + payload
