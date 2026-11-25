"""Compare and mask native percentages without changing the paper's original spelling."""
import re
import unicodedata


def _pattern(number):
    return re.compile(rf"(?<![\d.])(?:({number})\s*[-–—]\s*)?({number})\s*(?:[%％]|per\s*cent\b)", re.I)


_WITH_COMMAS = _pattern(r"\d+(?:\.\d+|,[ \t]*\d+)?")
_WITH_POINTS = _pattern(r"\d+(?:\.\d+)?")
_GROUP = r"(?:[IVX]{1,7}|(?:group|arm|grade|stage)\s+[A-Z0-9]+)(?:级)?"
_PAIRED_GROUP = re.compile(
    rf"{_GROUP}\s*[(（]\s*\d+(?:[.,]\d+)?\s*[%％]\s*[)）]\s*"
    rf"(?:and|or|versus|vs\.?|和|及|与|或|、)\s*{_GROUP}\s*[(（]\s*$", re.I)
_LIST = re.compile(r"(?:\bincl\.|\bincluding|\be\.g\.|\bsuch as|\bfor example|包括|例如|如)\s*$", re.I)


def _matches(text):
    for match in _WITH_COMMAS.finditer(text):
        values = [v for v in match.groups() if v]
        if not any("," in v for v in values):
            yield match, values
            continue
        before = text[:match.start()]
        # A split decimal is ambiguous in an English list. Join it only
        # inside one of a matched pair of group percentages, such as
        # grade II (30.1%) and III (20, 7%).
        paired = _PAIRED_GROUP.search(before[-150:]) and re.match(r"\s*[)）]", text[match.end():])
        in_list = _LIST.search(before[-25:]) or re.search(r"\d\s*,\s*$",before[-12:])
        if in_list or (any(re.search(r",\s",v) for v in values) and not paired):
            for plain in _WITH_POINTS.finditer(text, match.start(), match.end()):
                yield plain, [v for v in plain.groups() if v]
            continue
        # Without locale information, 1,250% can be a thousands grouping or
        # a three-place decimal. Require its exact value spelling on replay.
        canonical = [v if re.search(r",\s*\d{3}$",v) else re.sub(r",\s*", ".", v) for v in values]
        yield match, canonical


def percentages(text):
    text = unicodedata.normalize("NFKC", text)
    return {value for _, values in _matches(text) for value in values}


def quantities_match(source, translated):
    expected, actual = percentages(source), percentages(translated)
    if expected == actual:
        return True
    # Some additional digits may have been hidden by native formula tokens.
    return not (expected - actual) and bool(re.search(r"\{v\d+\}\s*%", source))


def replace_percentages(text, replace):
    """Substitute each recognized span; the callback gets the verbatim source."""
    parts, start = [], 0
    for match, _ in _matches(text):
        parts.extend((text[start:match.start()], replace(match)))
        start = match.end()
    return "".join([*parts, text[start:]])
