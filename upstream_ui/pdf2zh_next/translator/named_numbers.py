"""Repair an unambiguous scale error while preserving valid Chinese number forms."""
from decimal import Decimal
import re
import unicodedata


_NUMBER = r"(?<![A-Za-z\d.．])(?<!\d[,，])(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:[.．]\d+)?"
_EN = re.compile(rf"({_NUMBER})\s+(thousand|million|billion)\b", re.I)
_ZH = re.compile(rf"({_NUMBER})\s*(万亿|千亿|百亿|十亿|千万|百万|十万|亿|万|千|百)(?![十百千万亿]|分之)")
_EN_SCALE = {"thousand": 1000, "million": 10**6, "billion": 10**9}
_ZH_SCALE = {"百": 100, "千": 1000, "万": 10**4, "十万": 10**5, "百万": 10**6,
             "千万": 10**7, "亿": 10**8, "十亿": 10**9, "百亿": 10**10,
             "千亿": 10**11, "万亿": 10**12}
_RANGE = r"(?:[-–—~～±/、]|\b(?:and|or|through|to)\b|至|到|和|或)"


def _value(text):
    return Decimal(unicodedata.normalize("NFKC", text).replace(",", ""))


def _shared_unit(text, match):
    before, after = text[:match.start()], text[match.end():]
    return (re.search(rf"\d\s*{_RANGE}\s*(?:about\s+|约)?$", before, re.I)
            or re.match(rf"\s*{_RANGE}\s*(?:about\s+|约)?\d", after, re.I))


def preserve_named_number(source, translated):
    """Change just the numeric span when the same unique coefficient has the wrong unit."""
    english = list(_EN.finditer(source))
    if len(english) != 1:
        return translated
    chinese = list(_ZH.finditer(translated))
    if len(chinese) != 1:
        return translated
    old, new = english[0], chinese[0]
    coef = _value(old[1])
    if not coef or len(coef.as_tuple().digits) > 16 or coef != _value(new[1]):
        return translated
    multiplier = _EN_SCALE[old[2].lower()]
    if multiplier == _ZH_SCALE[new[2]]:
        return translated
    for text, match in ((source, old), (translated, new)):
        if _shared_unit(text, match) or sum(_value(n) == coef for n in re.findall(_NUMBER, text)) != 1:
            return translated
    total = coef * multiplier
    if any(_value(n) == total for n in re.findall(_NUMBER, translated)):
        return translated
    if total >= 10**8:
        divisor, suffix = 10**8, "亿"
    elif total >= 10**4:
        divisor, suffix = 10**4, "万"
    else:
        divisor, suffix = 1, ""
    amount = format((total / divisor).normalize(), "f") + suffix
    return translated[:new.start()] + amount + translated[new.end():]
