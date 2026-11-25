"""Measure completed translation work by source text as well as native item count."""
from collections import Counter
from functools import wraps
import json
from pathlib import Path
import re
import threading


def _key(text):
    # The term tracker contains plain PDF text. The translation driver replaces
    # formulas with placeholders and adds rich-text tags to the same paragraphs.
    plain = re.sub(r"<[^>]*>|\{v\d+\}", " ", text)
    return "".join(re.findall(r"[A-Za-z]+", plain)).lower()


class InferenceWork:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.seen = Counter()
        self.expected = None
        self.total = 0
        self.lock = threading.Lock()

    def _record(self, sources):
        keys = [_key(source) for source in sources if isinstance(source, str)]
        with self.lock:
            self.seen.update(k for k in keys if k)

    def attach(self, translator):
        # Both calls run in BabelDOC's child. Report after they return, including
        # native cache hits. Retries cannot exceed the source occurrence count.
        translate = translator.translate
        llm_translate = translator.llm_translate

        @wraps(translate)
        def single(text, *args, **kwargs):
            result = translate(text, *args, **kwargs)
            if result and isinstance(text, str):
                self._record([text])
            return result

        @wraps(llm_translate)
        def batch(text, *args, **kwargs):
            result = llm_translate(text, *args, **kwargs)
            if not result or not isinstance(text, str):
                return result
            from review import BATCH_INPUT, batch_items
            if BATCH_INPUT in text:
                self._record([row["input"] for row in batch_items(text)])
            elif "Now translate the following text:" in text:
                self._record([text.rpartition("Now translate the following text:")[2]])
            return result

        translator.translate = single
        translator.llm_translate = batch

    def fraction(self):
        if self.expected is None:
            # Written by the term extraction stage before paragraph translation.
            files = list(self.directory.glob("*/term_extractor_tracking.json"))
            if len(files) != 1:
                return None
            try:
                data = json.loads(files[0].read_text(encoding="utf-8"))
                expected = Counter(_key(source) for page in data["page"] for group in page["paragraph"]
                                   for source in group["pdf_unicodes"] if isinstance(source, str))
            except (OSError, UnicodeError, KeyError, TypeError, ValueError):
                return None
            expected.pop("", None)
            self.expected = expected
            self.total = sum((len(key) + 12) * count for key, count in expected.items())
        if not self.total:
            return None
        with self.lock:
            done = sum((len(key) + 12) * min(actual, self.expected.get(key, 0))
                       for key, actual in self.seen.items())
        return done / self.total
