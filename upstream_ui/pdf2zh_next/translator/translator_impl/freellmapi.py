"""FreeLLMAPI transport using Next's OpenAI prompts and response parsing."""
import json
import re
import threading
from urllib.parse import urlsplit

import httpx
import openai

from pdf2zh_next.config.translate_engine_model import OpenAISettings
from upstream_runtime.percentages import percentages, quantities_match, replace_percentages
from pdf2zh_next.translator.named_numbers import preserve_named_number
from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator


# A model may silently "correct" a study-arm abbreviation using one from the
# glossary. Keep the order and source labels in explicit two-arm comparisons.
_ARM_PAIR = re.compile(
    r"(?<![A-Za-z0-9_-])([A-Z][A-Z0-9_-]{1,15})(\s+(?i:vs\.?|versus)\s+)"
    r"([A-Z][A-Z0-9_-]{1,15})(?![A-Za-z0-9_-])"
)


def preserve_arm_pairs(source, target):
    expected = _ARM_PAIR.findall(source)
    actual = _ARM_PAIR.findall(target)
    if not expected or len(expected) != len(actual):
        return target
    pairs = iter(expected)

    def restore(match):
        left, _, right = next(pairs)
        if left == match[1] or right == match[3]:
            return left + match[2] + right
        return match[0]

    return _ARM_PAIR.sub(restore, target)


def preserve_batch_arm_pairs(prompt, reply):
    marker = "## Here is the input:"
    if not reply or marker not in prompt or not _ARM_PAIR.search(prompt.rpartition(marker)[2]):
        return reply
    try:
        items = json.loads(prompt.rpartition(marker)[2])
        outputs = json.loads(reply)
        if not isinstance(items, list) or not isinstance(outputs, list):
            return reply
        inputs = {item["id"]: item["input"] for item in items}
        changed = False
        for item in outputs:
            fixed = preserve_arm_pairs(inputs[item["id"]], item["output"])
            if fixed != item["output"]:
                item["output"] = fixed
                changed = True
        return json.dumps(outputs, ensure_ascii=False) if changed else reply
    except (KeyError, TypeError, ValueError):
        # Next handles malformed JSON and invokes its own paragraph fallback.
        return reply


_BATCH_INPUT = "## Here is the input:"
_SINGLE_INPUT = "Now translate the following text:"


_TAG = re.compile(r"</?([A-Za-z][\w:-]*)(?:\s+[^<>]*)?\s*/?>")


def retained_rich_label(source, translated):
    # Short mathematical/table labels can be safer verbatim than an invented
    # tag (e.g. a model converting italic p in p-Value into literal <p>).
    if "<style " not in source or len(_TAG.sub("", source)) > 50:
        return translated
    original_tags = {tag.lower() for tag in _TAG.findall(source)}
    new_tags = {tag.lower() for tag in _TAG.findall(translated)} - original_tags
    return source if new_tags else translated


def source_verified_percent_spelling(source, translation):
    # Some models print "%73" in Chinese. Only normalize a leading sign when
    # the original has that exact percentage and all percentage checks then pass.
    if not isinstance(translation, str) or quantities_match(source, translation):
        return translation
    expected = percentages(source)
    def restore(match):
        digits = match.group(1)
        return digits + "%" if digits in expected else match.group(0)
    rewritten = re.sub(r"(?<![0-9.])[%％]\s*([0-9]+(?:\.[0-9]+)?)(?![0-9.%％])", restore, translation)
    return rewritten if quantities_match(source, rewritten) else translation


def source_sentences(text):
    # Avoid breaking decimals, abbreviated comparisons and author citations.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    result = []
    for part in parts:
        if result and re.search(r"\b(?:vs|Fig|Dr|al|e\.g|i\.e)\.$", result[-1], re.I):
            result[-1] += " " + part
        else:
            result.append(part)
    return result


def numeric_literal(source):
    # Preserve untranslated statistical cells byte for byte. Any word, unit
    # name or native style/formula marker takes the full translation path.
    return (isinstance(source, str) and any(c.isdecimal() for c in source)
            and re.fullmatch(r"[0-9\s.,:;()\[\]%‰+−–—/×÷<>=±≤≥*⁎†‡-]+", source) is not None)


def unchanged_literal(source):
    if numeric_literal(source):
        return True
    if not isinstance(source, str):
        return False
    # Native equation tokens can stand for an operator, e.g. the equals sign.
    # Only preserve entire symbol expressions; accompanying prose still needs translation.
    symbols = re.sub(r"\{v\d+\}", " ", source)
    if not re.search(r"[=+*/÷×−≤≥<>^±-]", symbols):
        return False
    if not re.fullmatch(r"[A-Za-z0-9Α-Ωα-ω\s.,()\[\]_%=+*/÷×−≤≥<>^±-]+", symbols):
        return False
    names = re.findall(r"[A-Za-zΑ-Ωα-ω]+", symbols)
    return bool(names) and all(len(name) == 1 or name in {"sin", "cos", "tan", "ln", "log", "exp", "sqrt"}
                               for name in names)


def numeric_reply(prompt):
    if not isinstance(prompt, str):
        return None
    if _BATCH_INPUT in prompt:
        try:
            rows = json.loads(prompt.rpartition(_BATCH_INPUT)[2])
            if isinstance(rows,list) and rows and all(unchanged_literal(row["input"]) for row in rows):
                return json.dumps([{"id":row["id"], "output":row["input"]} for row in rows], ensure_ascii=False)
        except (KeyError, TypeError, ValueError):
            pass
    elif _SINGLE_INPUT in prompt:
        source = prompt.rpartition(_SINGLE_INPUT)[2].strip()
        if unchanged_literal(source):
            return source
    return None


class FreeLLMAPITranslator(OpenAITranslator):
    name = "FreeLLMAPI"
    accepts_boundary_context = True

    def __init__(self, settings, rate_limiter):
        config = settings.translate_engine_settings
        self.name = "原译翻译接口"
        adapted = settings.model_copy()
        adapted.translate_engine_settings = OpenAISettings(
            openai_model=config.freellmapi_model,
            openai_base_url=config.freellmapi_base_url,
            openai_api_key=config.freellmapi_api_key or "loopback",
        )
        super().__init__(adapted, rate_limiter)
        if config._direct_local:
            previous = self.client
            self.client = previous.with_options(max_retries=0, timeout=300,
                http_client=httpx.Client(trust_env=False, follow_redirects=False))
            previous.close()
            if not config.freellmapi_api_key:
                self.options["extra_headers"] = {"Authorization": openai.Omit()}
        else:
            loopback = urlsplit(config.freellmapi_base_url).hostname in ("127.0.0.1", "localhost", "::1")
            timeout = 300 if loopback and config.freellmapi_model != "auto" else 120
            self.client = self.client.with_options(max_retries=0, timeout=timeout)
        if config._direct_api:
            from upstream_runtime.endpoints import chat_generation_options
            provider_options = chat_generation_options(config.freellmapi_base_url, config.freellmapi_model)
            if provider_options:
                self.options["extra_body"] = provider_options
                self.add_cache_impact_parameters("generation", provider_options)
        self.add_cache_impact_parameters("endpoint", config.freellmapi_base_url)
        self.terminal_gateway_error = ""
        self._initial_request = threading.Lock()
        self._ready = False

    def _request(self, operation, text, params):
        try:
            result = operation(self, text, params)
            if not result:
                raise ValueError
            return result
        except openai.APIStatusError as error:
            hints = {
                401: "请检查当前服务的 API 密钥。",
                402: "请检查账户余额或剩余额度。",
                403: "请检查此密钥是否有权访问所选模型。",
                404: "请检查服务地址与模型 ID。",
                429: "请求过于频繁或额度已用尽，请稍后重试。",
            }
            hint = hints.get(error.status_code, "请检查服务商的运行状态。")
            self.terminal_gateway_error = f"{self.name} HTTP {error.status_code}。{hint}"
        except (openai.APITimeoutError, httpx.ReadTimeout):
            self.terminal_gateway_error = f"{self.name}响应超时，请检查模型负载后重试。"
        except (openai.APIConnectionError, httpx.ReadError, httpx.RemoteProtocolError):
            self.terminal_gateway_error = f"无法连接{self.name}，请检查服务地址与网络。"
        except (openai.OpenAIError, IndexError, AttributeError, ValueError):
            self.terminal_gateway_error = f"{self.name}未返回有效译文，请检查模型与服务状态。"
        raise RuntimeError(self.terminal_gateway_error) from None

    def _call(self, operation, text, params):
        if not self._ready:
            with self._initial_request:
                if self.terminal_gateway_error:
                    raise RuntimeError(self.terminal_gateway_error)
                if not self._ready:
                    result = self._request(operation, text, params)
                    self._ready = True
                    return result
        if self.terminal_gateway_error:
            raise RuntimeError(self.terminal_gateway_error)
        return self._request(operation, text, params)

    def _strict_quantity_retry(self, sentence, ignore_cache, rate_limit_params):
        # A model may quote a neighbouring percentage while translating a
        # clipped PDF fragment. Give this repair only the exact source slice.
        captured = []
        def protect(match):
            marker = f"[[PERCENT_{len(captured)}]]"
            captured.append((marker, match.group(0)))
            return marker
        if "[[PERCENT_" in sentence:
            return None
        masked = replace_percentages(sentence, protect)
        prompt = ("将输入的医学文献片段逐字义翻译为简体中文。输入有时仅为半句话或半个单词。"
                  "到原文末尾立即停止；保留未完整单词的英文拼写，不推测下半句。"
                  "只能用输入中的信息、数字和占位符，保留方括号标记。相对风险增加应表达为风险升高。"
                  "仅输出此片段的译文，不加说明。\n输入：" + masked)
        translated = super().llm_translate(prompt, ignore_cache, rate_limit_params)
        if not isinstance(translated,str) or not translated.strip():
            return None
        if any(translated.count(marker) != 1 for marker, _ in captured):
            return None
        for marker, original in captured:
            translated = translated.replace(marker,original)
        if (quantities_match(sentence,translated)
                and sorted(re.findall(r"\{v\d+\}",sentence)) == sorted(re.findall(r"\{v\d+\}",translated))):
            return translated
        return None

    def _recover_quantities(self, source, draft, ignore_cache, rate_limit_params):
        if unchanged_literal(source):
            return source
        draft = source_verified_percent_spelling(source, preserve_named_number(source, retained_rich_label(source, draft)))
        if quantities_match(source, draft):
            return draft
        sentences = source_sentences(source)
        repaired = []
        for sentence in sentences:
            # Scientific accuracy matters more than one fewer model call on a
            # failed paragraph. Each redraft has the entire paragraph for
            # context but returns plain text, without a fragile JSON wrapper.
            prompt = (
                "任务：将指定的一个英文科研句子译成学术简体中文。整段上下文仅供理解。"
                "只输出指定句子的译文，其他句子一律不输出。不要添加解释或引号。"
                "用阿拉伯数字和 % 原样保留本句的百分数，不增加其他句子的数值。"
                "原样保留所有 {v数字} 占位符、style 标签、研究组缩写。\n"
                "【整段上下文】\n" + source + "\n【本次只译这一句】\n" + sentence
            )
            output = super().llm_translate(prompt, ignore_cache, rate_limit_params)
            output = source_verified_percent_spelling(sentence, output)
            if (not isinstance(output, str) or not output.strip()
                    or not quantities_match(sentence, output)
                    or sorted(re.findall(r"\{v\d+\}", sentence)) != sorted(re.findall(r"\{v\d+\}", output))):
                output = self._strict_quantity_retry(sentence, ignore_cache, rate_limit_params)
            if not output:
                excerpt = " ".join(sentence.split())[:78]
                self.terminal_gateway_error = f"段落复译后仍无法核实百分比或占位符。请改选模型后重试。原文片段「{excerpt}…」"
                raise RuntimeError(self.terminal_gateway_error)
            repaired.append(preserve_named_number(sentence, preserve_arm_pairs(sentence, output.strip())))
        return "".join(repaired)

    def llm_translate(self, text, ignore_cache=False, rate_limit_params=None):
        from retainpdf_support.prompt import add_boundary_context
        literal = numeric_reply(text)
        if literal is not None:
            return literal
        text = add_boundary_context(text, getattr(self, "boundary_index", None))
        result = super().llm_translate(text, ignore_cache, rate_limit_params)
        if not result or not text:
            return result
        if _BATCH_INPUT not in text:
            if _SINGLE_INPUT in text:
                source = text.rpartition(_SINGLE_INPUT)[2].strip()
                return self._recover_quantities(source, preserve_arm_pairs(source, result), ignore_cache, rate_limit_params)
            return result
        result = preserve_batch_arm_pairs(text, result)
        try:
            inputs = {r["id"]: r["input"] for r in json.loads(text.rpartition(_BATCH_INPUT)[2])}
            items = json.loads(result)
            if not isinstance(items, list):
                return result
            changed = False
            for item in items:
                repaired = self._recover_quantities(inputs[item["id"]], item["output"], ignore_cache, rate_limit_params)
                if item["output"] != repaired:
                    item["output"] = repaired
                    changed = True
            return json.dumps(items, ensure_ascii=False) if changed else result
        except (KeyError, TypeError, ValueError):
            return result  # Next will retry malformed batches per paragraph.

    def translate(self, text, ignore_cache=False, rate_limit_params=None):
        if unchanged_literal(text):
            return text
        result = super().translate(text, ignore_cache, rate_limit_params)
        return preserve_named_number(text, retained_rich_label(text, preserve_arm_pairs(text, result))) if result else result

    def do_translate(self, text, rate_limit_params=None):
        # Bypass only upstream's hundred-attempt rate-limit retry wrapper.
        return self._call(OpenAITranslator.do_translate.__wrapped__, text, rate_limit_params)

    def do_llm_translate(self, text, rate_limit_params=None):
        if text is None:
            return None
        return self._call(OpenAITranslator.do_llm_translate.__wrapped__, text, rate_limit_params)
